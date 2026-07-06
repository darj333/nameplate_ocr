"""Background processing pipeline: image/PDF → Groq vision LLM → DB."""
from __future__ import annotations

import io
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

def _process_pdf(nameplate: Nameplate, db: Session) -> None:
    """
    For each page of the PDF:
      - Call the table-extraction LLM prompt.
      - For every row in every table on that page, create one Nameplate record
        whose attributes are the table columns (plus "Table" and "Page" columns
        for context).

    The original *nameplate* record is used for the very first row extracted.
    If no rows are found on a page, the page is skipped silently.
    """
    file_path = nameplate.file_path
    base_name = Path(file_path).stem  # PDF filename without extension

    logger.info("[%d] Rendering PDF: %s", nameplate.id, file_path)
    pages = _render_pdf_pages(file_path)
    logger.info("[%d] PDF has %d page(s)", nameplate.id, len(pages))

    first_record_used = False
    total_rows = 0

    for page_idx, png_bytes in enumerate(pages):
        page_num = page_idx + 1
        logger.info("[%d] Extracting tables from page %d/%d", nameplate.id, page_num, len(pages))

        try:
            tables, raw = extract_tables_from_pdf_page(png_bytes)
        except Exception as exc:
            logger.warning("[%d] Page %d table extraction failed: %s", nameplate.id, page_num, exc)
            continue

        for table in tables:
            title = table["title"] or f"Page {page_num}"
            rows = table["rows"]
            logger.info("[%d] Table %r: %d row(s)", nameplate.id, title, len(rows))

            for row_idx, row in enumerate(rows):
                # Build attribute list: table columns first, then context attrs
                pairs: list[dict[str, str]] = []
                for col, val in row.items():
                    v = str(val).strip()
                    if v:
                        pairs.append({"name": col, "value": v})

                if not pairs:
                    continue

                # Add context so it's easy to trace back to the source
                pairs.append({"name": "Table", "value": title})
                if len(pages) > 1:
                    pairs.append({"name": "Page", "value": str(page_num)})
                pairs.append({"name": "Source", "value": nameplate.filename})

                # Derive a meaningful record filename
                nr = row.get("Nr. Crt.", row.get("Nr.", row.get("#", "")))
                row_label = f"row {nr}" if nr else f"row {row_idx + 1}"
                record_name = f"{base_name} — {title} — {row_label}"

                if not first_record_used:
                    # Reuse the placeholder record that was created on upload
                    nameplate.filename = record_name
                    db.commit()
                    _save_attributes(nameplate, pairs, raw, db)
                    first_record_used = True
                else:
                    _create_record(record_name, file_path, pairs, raw, db)

                total_rows += 1

    if total_rows == 0:
        # No table rows found at all — mark the original record as failed
        nameplate.status = "failed"
        nameplate.error_message = "No table rows could be extracted from this PDF."
        db.commit()
        logger.warning("[%d] No rows extracted from PDF", nameplate.id)
    else:
        logger.info("[%d] PDF pipeline complete — %d row record(s) created", nameplate.id, total_rows)


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
