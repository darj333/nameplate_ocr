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

# Each exported column mapped to the keywords that recognise it. The vision model
# returns descriptive Title Case names (e.g. "Rated Current", "Power Factor"),
# which these matchers map to the canonical column by meaning. `include` keywords
# score a candidate attribute name; `exclude` keywords disqualify it
# (e.g. "starting current" must not satisfy nominal current).
FIELD_MATCHERS: dict[str, dict[str, list[str]]] = {
    "In [A]":  {"include": ["current", "amp", "ampere", "strom"],
                "exclude": ["start", "inrush", "locked", "no-load", "no load"]},
    "Un [kV]": {"include": ["voltage", "volt", "spannung"],
                "exclude": ["current"]},
    "ηn [%]":  {"include": ["efficien", "η", "eta", "wirkungsgrad"],
                "exclude": []},
    "cosφn":   {"include": ["power factor", "cos", "cosφ", "cos φ", "leistungsfaktor"],
                "exclude": []},
    "n [rpm]": {"include": ["speed", "rpm", "revolut", "drehzahl"],
                "exclude": []},
}
# Export column order (Filename and Uploaded At are prepended at build time).
EXPORT_FIELDS: list[str] = list(FIELD_MATCHERS)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_ids(ids_str: Optional[str]) -> list[int] | None:
    if not ids_str:
        return None
    try:
        return [int(i.strip()) for i in ids_str.split(",") if i.strip()]
    except ValueError:
        return None


def _best_value(attr_map: dict[str, str], include: list[str], exclude: list[str]) -> str:
    """
    Pick the attribute value whose name best matches the *include* keywords.
    Returns "" when no attribute on the nameplate matches this field.
    """
    best_score = 0
    best_value = ""
    for name, value in attr_map.items():
        low = name.lower()
        if any(ex in low for ex in exclude):
            continue
        score = sum(1 for kw in include if kw in low)
        if score > best_score:
            best_score = score
            best_value = value
    return best_value


def _build_dataframe(db: Session, ids: list[int] | None) -> pd.DataFrame:
    columns = ["Filename", "Uploaded At", *EXPORT_FIELDS]

    query = db.query(Nameplate)
    if ids:
        query = query.filter(Nameplate.id.in_(ids))
    records = query.order_by(Nameplate.id).all()

    if not records:
        return pd.DataFrame(columns=columns)

    record_ids = [rec.id for rec in records]
    attrs = (
        db.query(NameplateAttribute)
        .filter(NameplateAttribute.nameplate_id.in_(record_ids))
        .all()
    )

    attr_map: dict[int, dict[str, str]] = {rid: {} for rid in record_ids}
    for a in attrs:
        attr_map[a.nameplate_id][a.attribute_name] = a.attribute_value or ""

    rows: list[dict] = []
    for rec in records:
        amap = attr_map[rec.id]
        row: dict = {
            "Filename": rec.filename,
            "Uploaded At": rec.uploaded_at,
        }
        for field, matcher in FIELD_MATCHERS.items():
            row[field] = _best_value(amap, matcher["include"], matcher["exclude"])
        rows.append(row)

    return pd.DataFrame(rows, columns=columns)


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
