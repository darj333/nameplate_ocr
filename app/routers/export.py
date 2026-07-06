"""CSV and Excel export endpoints."""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.nameplate import Nameplate, NameplateAttribute

router = APIRouter(prefix="/export", tags=["export"])


def _build_wide_dataframe(db: Session, ids: list[int] | None) -> pd.DataFrame:
    """
    Pivot the key/value attribute table into one row per nameplate,
    one column per unique attribute name.
    """
    query = db.query(Nameplate)
    if ids:
        query = query.filter(Nameplate.id.in_(ids))
    records = query.order_by(Nameplate.id).all()

    if not records:
        return pd.DataFrame()

    # Metadata columns
    meta_rows = [
        {
            "ID": rec.id,
            "Filename": rec.filename,
            "Uploaded At": rec.uploaded_at,
            "Status": rec.status,
        }
        for rec in records
    ]

    # Attributes pivot
    record_ids = [rec.id for rec in records]
    attrs = (
        db.query(NameplateAttribute)
        .filter(NameplateAttribute.nameplate_id.in_(record_ids))
        .all()
    )

    # Build {nameplate_id: {attr_name: value}}
    attr_map: dict[int, dict[str, str]] = {rid: {} for rid in record_ids}
    for a in attrs:
        attr_map[a.nameplate_id][a.attribute_name] = a.attribute_value or ""

    # Merge meta + attrs
    rows = []
    for meta, rid in zip(meta_rows, record_ids):
        row = dict(meta)
        row.update(attr_map[rid])
        rows.append(row)

    df = pd.DataFrame(rows)
    # Put metadata columns first
    meta_cols = ["ID", "Filename", "Uploaded At", "Status"]
    attr_cols = sorted(c for c in df.columns if c not in meta_cols)
    return df[meta_cols + attr_cols]


def _parse_ids(ids_str: Optional[str]) -> list[int] | None:
    if not ids_str:
        return None
    try:
        return [int(i.strip()) for i in ids_str.split(",") if i.strip()]
    except ValueError:
        return None


# ── CSV ───────────────────────────────────────────────────────────────────────

@router.get("/csv")
def export_csv(
    ids: Optional[str] = Query(None, description="Comma-separated nameplate IDs"),
    db: Session = Depends(get_db),
):
    df = _build_wide_dataframe(db, _parse_ids(ids))

    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nameplates.csv"},
    )


# ── Excel ─────────────────────────────────────────────────────────────────────

@router.get("/xlsx")
def export_xlsx(
    ids: Optional[str] = Query(None, description="Comma-separated nameplate IDs"),
    db: Session = Depends(get_db),
):
    df = _build_wide_dataframe(db, _parse_ids(ids))

    # openpyxl cannot write timezone-aware datetimes — strip tz info first
    if "Uploaded At" in df.columns:
        df["Uploaded At"] = pd.to_datetime(df["Uploaded At"]).dt.tz_localize(None)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Nameplates")

        # Auto-size columns
        ws = writer.sheets["Nameplates"]
        for col in ws.columns:
            max_len = max(
                (len(str(cell.value)) for cell in col if cell.value is not None),
                default=10,
            )
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

    output.seek(0)
    return StreamingResponse(
        iter([output.read()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=nameplates.xlsx"},
    )
