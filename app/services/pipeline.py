"""Background processing pipeline: image → Groq vision LLM → DB."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.nameplate import Nameplate, NameplateAttribute
from app.services.llm import structure_with_llm, structure_with_llm_bytes

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── Image processing ───────────────────────────────────────────────────────────

def _process_image(nameplate: Nameplate, db: Session) -> None:
    logger.info("[%d] Sending image to vision LLM: %s", nameplate.id, nameplate.file_path)
    # Prefer the stored bytes (DB is source of truth); fall back to disk for
    # legacy rows that predate image_data.
    if nameplate.image_data:
        pairs, raw = structure_with_llm_bytes(nameplate.image_data, nameplate.image_mime)
    else:
        pairs, raw = structure_with_llm(nameplate.file_path)
    logger.info("[%d] LLM returned %d attribute pairs", nameplate.id, len(pairs))
    _save_attributes(nameplate, pairs, raw, db)


# ── Entry point ────────────────────────────────────────────────────────────────

def process_nameplate(nameplate_id: int, db: Session) -> None:
    """Send the uploaded image to the vision LLM and persist its attributes."""
    nameplate: Nameplate | None = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        logger.error("process_nameplate: id=%d not found", nameplate_id)
        return

    try:
        _process_image(nameplate, db)
    except Exception as exc:
        logger.exception("[%d] Pipeline failed: %s", nameplate_id, exc)
        try:
            nameplate.status = "failed"
            nameplate.error_message = str(exc)[:1000]
            db.commit()
        except Exception:
            db.rollback()
