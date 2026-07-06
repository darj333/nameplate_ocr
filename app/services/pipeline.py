"""Background processing pipeline: image → Groq vision LLM → DB."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.nameplate import Nameplate, NameplateAttribute
from app.services.llm import structure_with_llm

logger = logging.getLogger(__name__)


def process_nameplate(nameplate_id: int, db: Session) -> None:
    """
    Send the uploaded image directly to the Groq vision model,
    parse the returned attributes, and persist them.
    """
    nameplate: Nameplate | None = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        logger.error("process_nameplate: id=%d not found", nameplate_id)
        return

    try:
        logger.info("[%d] Sending image to vision LLM: %s", nameplate_id, nameplate.file_path)
        pairs, raw_response = structure_with_llm(nameplate.file_path)
        logger.info("[%d] LLM returned %d attribute pairs", nameplate_id, len(pairs))

        # Store the raw LLM response in ocr_raw_text for debugging / reprocessing
        nameplate.ocr_raw_text = raw_response

        # Replace any existing attributes
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
