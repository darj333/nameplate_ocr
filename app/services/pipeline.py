"""Background processing pipeline: image/PDF → Groq vision LLM → DB."""
from __future__ import annotations

import io
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.nameplate import Nameplate, NameplateAttribute
from app.services.llm import structure_with_llm, structure_with_llm_bytes

logger = logging.getLogger(__name__)


def _render_pdf_pages(file_path: str | Path) -> list[bytes]:
    """
    Render every page of a PDF to PNG bytes using pypdfium2.
    Returns a list of PNG byte strings, one per page.
    """
    import pypdfium2 as pdfium  # lazy import — only needed for PDFs

    pdf = pdfium.PdfDocument(str(file_path))
    pages_png: list[bytes] = []
    for page in pdf:
        bitmap = page.render(scale=2)  # 2× scale ≈ 144 dpi — good for OCR
        pil_image = bitmap.to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        pages_png.append(buf.getvalue())
    return pages_png


def _persist_attributes(
    nameplate: Nameplate,
    pairs: list[dict[str, str]],
    raw_response: str,
    db: Session,
) -> None:
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


def process_nameplate(nameplate_id: int, db: Session) -> None:
    """
    Process a single nameplate record.

    - Image files: sent directly to the Groq vision model.
    - PDF files: each page is rendered to PNG in memory, then each page is
      sent to the vision model. The first page updates this record; additional
      pages create new sibling records sharing the same original filename
      (suffixed with the page number).
    """
    nameplate: Nameplate | None = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        logger.error("process_nameplate: id=%d not found", nameplate_id)
        return

    try:
        file_path = Path(nameplate.file_path)
        is_pdf = file_path.suffix.lower() == ".pdf"

        if not is_pdf:
            logger.info("[%d] Sending image to vision LLM: %s", nameplate_id, file_path)
            pairs, raw_response = structure_with_llm(file_path)
            logger.info("[%d] LLM returned %d attribute pairs", nameplate_id, len(pairs))
            _persist_attributes(nameplate, pairs, raw_response, db)

        else:
            logger.info("[%d] Rendering PDF pages: %s", nameplate_id, file_path)
            pages = _render_pdf_pages(file_path)
            logger.info("[%d] PDF has %d page(s)", nameplate_id, len(pages))

            base_filename = nameplate.filename

            for page_idx, png_bytes in enumerate(pages):
                page_num = page_idx + 1
                logger.info("[%d] Processing PDF page %d/%d", nameplate_id, page_num, len(pages))

                pairs, raw_response = structure_with_llm_bytes(png_bytes, "image/png")
                logger.info(
                    "[%d] Page %d: LLM returned %d attribute pairs",
                    nameplate_id, page_num, len(pairs),
                )

                if page_idx == 0:
                    # Update the original record for page 1
                    _persist_attributes(nameplate, pairs, raw_response, db)
                    db.refresh(nameplate)
                else:
                    # Create a new record for every subsequent page
                    sibling = Nameplate(
                        filename=f"{base_filename} (page {page_num})",
                        file_path=nameplate.file_path,
                    )
                    db.add(sibling)
                    db.commit()
                    db.refresh(sibling)
                    _persist_attributes(sibling, pairs, raw_response, db)

        logger.info("[%d] Pipeline complete", nameplate_id)

    except Exception as exc:
        logger.exception("[%d] Pipeline failed: %s", nameplate_id, exc)
        try:
            nameplate.status = "failed"
            nameplate.error_message = str(exc)[:1000]
            db.commit()
        except Exception:
            db.rollback()
