"""CSV and Excel export endpoints."""
from __future__ import annotations

import io
import json
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.nameplate import Nameplate, NameplateAttribute

router = APIRouter(prefix="/export", tags=["export"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_ids(ids_str: Optional[str]) -> list[int] | None:
    if not ids_str:
        return None
    try:
        return [int(i.strip()) for i in ids_str.split(",") if i.strip()]
    except ValueError:
        return None


def _try_json_list(value: str) -> list[str] | None:
    """Return a list if *value* is a JSON array string, otherwise None."""
    if not value or not value.startswith("["):
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        return None


def _expand_record(meta: dict, attr_map: dict[str, str]) -> list[dict]:
    """
    For plain nameplate records: return a single dict (meta + attrs).
    For table records (attrs whose values are JSON arrays): zip the columns
    back into individual rows, one dict per data row.
    """
    table_cols: dict[str, list[str]] = {}
    plain_attrs: dict[str, str] = {}

    for name, value in attr_map.items():
        lst = _try_json_list(value or "")
        if lst is not None:
            table_cols[name] = lst
        else:
            plain_attrs[name] = value

    if not table_cols:
        return [{**meta, **plain_attrs}]

    row_count = max(len(v) for v in table_cols.values())
    rows = []
    for i in range(row_count):
        row = dict(meta)
        row.update(plain_attrs)
        for col, values in table_cols.items():
            row[col] = values[i] if i < len(values) else ""
        rows.append(row)
    return rows


def _build_dataframe(db: Session, ids: list[int] | None) -> pd.DataFrame:
    query = db.query(Nameplate)
    if ids:
        query = query.filter(Nameplate.id.in_(ids))
    records = query.order_by(Nameplate.id).all()

    if not records:
        return pd.DataFrame()

    record_ids = [rec.id for rec in records]
    attrs = (
        db.query(NameplateAttribute)
        .filter(NameplateAttribute.nameplate_id.in_(record_ids))
        .all()
    )

    attr_map: dict[int, dict[str, str]] = {rid: {} for rid in record_ids}
    for a in attrs:
        attr_map[a.nameplate_id][a.attribute_name] = a.attribute_value or ""

    all_rows: list[dict] = []
    for rec in records:
        meta = {
            "ID": rec.id,
            "Filename": rec.filename,
            "Uploaded At": rec.uploaded_at,
            "Status": rec.status,
        }
        all_rows.extend(_expand_record(meta, attr_map[rec.id]))

    df = pd.DataFrame(all_rows)
    meta_cols = ["ID", "Filename", "Uploaded At", "Status"]
    attr_cols = [c for c in df.columns if c not in meta_cols]
    return df[meta_cols + attr_cols]


# ── CSV ────────────────────────────────────────────────────────────────────────

@router.get("/csv")
def export_csv(
    ids: Optional[str] = Query(None, description="Comma-separated nameplate IDs"),
    db: Session = Depends(get_db),
):
    df = _build_dataframe(db, _parse_ids(ids))
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nameplates.csv"},
    )


# ── Excel ──────────────────────────────────────────────────────────────────────

@router.get("/xlsx")
def export_xlsx(
    ids: Optional[str] = Query(None, description="Comma-separated nameplate IDs"),
    db: Session = Depends(get_db),
):
    df = _build_dataframe(db, _parse_ids(ids))

    if "Uploaded At" in df.columns:
        df["Uploaded At"] = pd.to_datetime(df["Uploaded At"]).dt.tz_localize(None)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Nameplates")
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
