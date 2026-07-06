"""Background processing pipeline: image/PDF → Groq vision LLM → DB."""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.nameplate import Nameplate, NameplateAttribute
from app.services.llm import extract_tables_from_pdf_page, structure_with_llm

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _render_pdf_pages(file_path: str | Path) -> list[bytes]:
    """Render every PDF page to PNG bytes at 144 dpi via pypdfium2."""
    import pypdfium2 as pdfium  # lazy import — only needed for PDFs

    pdf = pdfium.PdfDocument(str(file_path))
    pages_png: list[bytes] = []
    for page in pdf:
        bitmap = page.render(scale=2)  # 2× = ~144 dpi
        pil_image = bitmap.to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        pages_png.append(buf.getvalue())
    return pages_png


def _save_attributes(
    nameplate: Nameplate,
    pairs: list[dict[str, str]],
    raw_response: str,
    db: Session,
) -> None:
    """Replace all attributes on *nameplate* with *pairs* and mark it processed."""
    nameplate.ocr_raw_text = raw_response
    db.query(NameplateAttribute).filter(
        NameplateAttribute.nameplate_id == nameplate.id
    ).delete()
    for pair in pairs:
        db.add(
            NameplateAttribute(
                nameplate_id=nameplate.id,
                attribute_name=pair["name"],
                attribute_value=pair["value"],
            )
        )
    nameplate.status = "processed"
    nameplate.error_message = None
    db.commit()


def _create_record(
    filename: str,
    file_path: str,
    pairs: list[dict[str, str]],
    raw_response: str,
    db: Session,
) -> Nameplate:
    """Create a new Nameplate record and persist its attributes."""
    np = Nameplate(filename=filename, file_path=file_path)
    db.add(np)
    db.commit()
    db.refresh(np)
    _save_attributes(np, pairs, raw_response, db)
    return np


# ── Image processing ───────────────────────────────────────────────────────────

def _process_image(nameplate: Nameplate, db: Session) -> None:
    logger.info("[%d] Sending image to vision LLM: %s", nameplate.id, nameplate.file_path)
    pairs, raw = structure_with_llm(nameplate.file_path)
    logger.info("[%d] LLM returned %d attribute pairs", nameplate.id, len(pairs))
    _save_attributes(nameplate, pairs, raw, db)


# ── PDF processing ─────────────────────────────────────────────────────────────

def _table_to_pairs(table: dict, source_filename: str) -> list[dict[str, str]]:
    """
    Convert a single extracted table into attribute pairs where each column
    is stored as a JSON array of its cell values (top to bottom).

    A downstream consumer (export, UI) can detect these JSON-array attributes
    and reconstruct the full table by zipping the columns back together.

    Special attributes added:
      "Source"  → original PDF filename (plain string)
    """
    rows = table["rows"]
    if not rows:
        return []

    # Stable column order: union of all keys in document order
    columns: list[str] = list(dict.fromkeys(
        col for row in rows for col in row.keys()
    ))

    pairs: list[dict[str, str]] = [
        {"name": "Source", "value": source_filename},
    ]

    for col in columns:
        values = [str(row.get(col, "")).strip() for row in rows]
        pairs.append({
            "name": col,
            "value": json.dumps(values, ensure_ascii=False),
        })

    return pairs


def _process_pdf(nameplate: Nameplate, db: Session) -> None:
    """
    For each page of the PDF extract every table.
    Each table becomes one Nameplate record named "<Table Title> — <PDF stem>".
    All rows of the table are stored as attributes of that single record.

    The original placeholder record (created on upload) is reused for the
    first table found; subsequent tables create new sibling records.
    """
    file_path = nameplate.file_path
    base_name = Path(file_path).stem
    original_filename = nameplate.filename

    logger.info("[%d] Rendering PDF: %s", nameplate.id, file_path)
    pages = _render_pdf_pages(file_path)
    logger.info("[%d] PDF has %d page(s)", nameplate.id, len(pages))

    first_record_used = False
    total_tables = 0

    for page_idx, png_bytes in enumerate(pages):
        page_num = page_idx + 1
        logger.info("[%d] Extracting tables from page %d/%d", nameplate.id, page_num, len(pages))

        try:
            tables, raw = extract_tables_from_pdf_page(png_bytes)
        except Exception as exc:
            logger.warning("[%d] Page %d extraction failed: %s", nameplate.id, page_num, exc)
            continue

        for table in tables:
            title = table["title"].strip()
            if not title:
                logger.warning(
                    "[%d] Page %d: skipping untitled table (%d rows). "
                    "Raw response snippet: %.200s",
                    nameplate.id, page_num, len(table.get("rows", [])), raw,
                )
                continue
            logger.info("[%d] Table %r: %d row(s)", nameplate.id, title, len(table["rows"]))

            pairs = _table_to_pairs(table, original_filename)
            if not pairs:
                continue

            record_name = f"{title} — {base_name}"

            if not first_record_used:
                nameplate.filename = record_name
                db.commit()
                _save_attributes(nameplate, pairs, raw, db)
                first_record_used = True
            else:
                _create_record(record_name, file_path, pairs, raw, db)

            total_tables += 1

    if total_tables == 0:
        nameplate.status = "failed"
        nameplate.error_message = "No tables could be extracted from this PDF."
        db.commit()
        logger.warning("[%d] No tables extracted from PDF", nameplate.id)
    else:
        logger.info("[%d] PDF pipeline complete — %d table record(s) created", nameplate.id, total_tables)


# ── Entry point ────────────────────────────────────────────────────────────────

def process_nameplate(nameplate_id: int, db: Session) -> None:
    """Dispatch to image or PDF pipeline based on file extension."""
    nameplate: Nameplate | None = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        logger.error("process_nameplate: id=%d not found", nameplate_id)
        return

    try:
        is_pdf = Path(nameplate.file_path).suffix.lower() == ".pdf"
        if is_pdf:
            _process_pdf(nameplate, db)
        else:
            _process_image(nameplate, db)
    except Exception as exc:
        logger.exception("[%d] Pipeline failed: %s", nameplate_id, exc)
        try:
            nameplate.status = "failed"
            nameplate.error_message = str(exc)[:1000]
            db.commit()
        except Exception:
            db.rollback()
