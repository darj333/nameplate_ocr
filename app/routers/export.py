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

# Each image-derived column mapped to the keywords that recognise it. The vision
# model returns descriptive Title Case names (e.g. "Rated Current", "Power
# Factor", "Manufacturer", "Type"); these matchers map that freeform name to the
# canonical column by meaning. `include` keywords are listed in priority order — a
# match against an earlier keyword always wins (so "Serial Number" beats "Article
# No"). `exclude` keywords disqualify a candidate (e.g. "starting current" for
# nominal current, "construction type" for model).
FIELD_MATCHERS: dict[str, dict[str, list[str]]] = {
    "Serial No": {
        "include": ["serial", "serial no", "serial number", "s/n", "seriennummer",
                    "product", "product no", "product id", "product code", "product number",
                    "article", "article no", "order no", "order number", "bestellnummer",
                    "material no", "material number", "materialnummer", "item no", "item number",
                    "ident"],
        "exclude": [],
    },
    "Brand": {
        "include": ["manufacturer", "brand", "make", "marque", "hersteller",
                    "fabrikat", "producer", "produced by", "company"],
        "exclude": [],
    },
    "Model": {
        "include": ["model", "model no", "model number", "type designation",
                    "designation", "type", "typ", "bezeichnung", "serie", "series",
                    "reference"],
        "exclude": ["protection", "construction", "cooling", "enclosure", "frame",
                    "bearing", "connection", "mounting", "insulation"],
    },
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

# Final export column order. "ID" is the DB record id; "Filename"/"Uploaded At"
# are record metadata; the rest are matched from the nameplate image.
EXPORT_COLUMNS: list[str] = [
    "ID", "Serial No", "Brand", "Model", "Filename", "Uploaded At",
    "In [A]", "Un [kV]", "ηn [%]", "cosφn", "n [rpm]",
]


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
    Pick the attribute whose name best matches the *include* keywords.
    `include` is priority-ordered: the candidate matching the earliest keyword
    wins (so "Serial Number" beats "Article No"). Ties are broken by number of
    matched keywords. Returns "" when no attribute matches this field.
    """
    best_value = ""
    best_key: tuple[int, int] | None = None
    for name in sorted(attr_map):
        low = name.lower()
        if any(ex in low for ex in exclude):
            continue
        matched = [i for i, kw in enumerate(include) if kw in low]
        if not matched:
            continue
        # (earliest keyword index, −match count): smaller is better
        key = (matched[0], -len(matched))
        if best_key is None or key < best_key:
            best_key = key
            best_value = attr_map[name]
    return best_value


def _build_dataframe(db: Session, ids: list[int] | None) -> pd.DataFrame:
    query = db.query(Nameplate)
    if ids:
        query = query.filter(Nameplate.id.in_(ids))
    records = query.order_by(Nameplate.id).all()

    if not records:
        return pd.DataFrame(columns=EXPORT_COLUMNS)

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
            "ID": rec.id,
            "Filename": rec.filename,
            "Uploaded At": rec.uploaded_at,
        }
        for field, matcher in FIELD_MATCHERS.items():
            row[field] = _best_value(amap, matcher["include"], matcher["exclude"])
        rows.append(row)

    return pd.DataFrame(rows, columns=EXPORT_COLUMNS)


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
