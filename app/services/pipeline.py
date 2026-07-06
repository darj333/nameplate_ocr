"""Background processing pipeline: OCR → LLM → DB."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.nameplate import Nameplate, NameplateAttribute
from app.services.ocr import run_ocr
from app.services.llm import structure_with_llm

logger = logging.getLogger(__name__)


def process_nameplate(nameplate_id: int, db: Session) -> None:
    """
    Full extraction pipeline for a single nameplate row.
    Updates *status* and *attributes* in-place.
    Called from a FastAPI BackgroundTask.
    """
    nameplate: Nameplate | None = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        logger.error("process_nameplate: id=%d not found", nameplate_id)
        return

    settings = get_settings()

    try:
        # ── Step 1: OCR ───────────────────────────────────────────────────────
        logger.info("[%d] Starting OCR on %s", nameplate_id, nameplate.file_path)
        raw_text = run_ocr(nameplate.file_path, languages=settings.ocr_languages)
        nameplate.ocr_raw_text = raw_text
        db.commit()
        logger.info("[%d] OCR complete (%d chars)", nameplate_id, len(raw_text))

        # ── Step 2: LLM structuring ───────────────────────────────────────────
        logger.info("[%d] Sending to LLM for structuring…", nameplate_id)
        pairs = structure_with_llm(raw_text)
        logger.info("[%d] LLM returned %d attribute pairs", nameplate_id, len(pairs))

        # ── Step 3: Persist attributes ────────────────────────────────────────
        db.query(NameplateAttribute).filter(
            NameplateAttribute.nameplate_id == nameplate_id
        ).delete()

        for pair in pairs:
            db.add(
                NameplateAttribute(
                    nameplate_id=nameplate_id,
                    attribute_name=pair["name"],
                    attribute_value=pair["value"],
                )
            )

        nameplate.status = "processed"
        nameplate.error_message = None
        db.commit()
        logger.info("[%d] Pipeline complete", nameplate_id)

    except Exception as exc:
        logger.exception("[%d] Pipeline failed: %s", nameplate_id, exc)
        try:
            nameplate.status = "failed"
            nameplate.error_message = str(exc)[:1000]
            db.commit()
        except Exception:
            db.rollback()
