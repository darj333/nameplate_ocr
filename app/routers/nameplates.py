"""Nameplate upload, retrieval, and attribute-correction endpoints."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.nameplate import Nameplate, NameplateAttribute
from app.schemas import (
    AttributeOut,
    AttributesBulkUpdate,
    NameplateListResponse,
    NameplateOut,
    NameplateSummary,
    UploadResponse,
)
from app.services.pipeline import process_nameplate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nameplates", tags=["nameplates"])

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
    "image/bmp",
    "application/pdf",
}


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_nameplate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{file.content_type}'. "
                   f"Allowed: {sorted(ALLOWED_CONTENT_TYPES)}",
        )

    # Size guard (read into memory once, then write)
    contents = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_upload_size_mb} MB limit.",
        )

    # Persist file
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename or 'upload').name}"
    dest = upload_dir / safe_name

    async with aiofiles.open(dest, "wb") as f:
        await f.write(contents)

    # DB record
    nameplate = Nameplate(filename=file.filename or safe_name, file_path=str(dest))
    db.add(nameplate)
    db.commit()
    db.refresh(nameplate)

    # Kick off background processing
    background_tasks.add_task(process_nameplate, nameplate.id, db)

    return UploadResponse(
        id=nameplate.id,
        status=nameplate.status,
        message="Upload accepted; extraction is running in the background.",
    )


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=NameplateListResponse)
def list_nameplates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    total = db.query(Nameplate).count()
    items = (
        db.query(Nameplate)
        .order_by(Nameplate.uploaded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return NameplateListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[NameplateSummary.model_validate(n) for n in items],
    )


# ── Single record ─────────────────────────────────────────────────────────────

@router.get("/{nameplate_id}", response_model=NameplateOut)
def get_nameplate(nameplate_id: int, db: Session = Depends(get_db)):
    nameplate = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        raise HTTPException(status_code=404, detail="Nameplate not found")
    return NameplateOut.model_validate(nameplate)


# ── Manual attribute correction ───────────────────────────────────────────────

@router.patch("/{nameplate_id}/attributes", response_model=list[AttributeOut])
def update_attributes(
    nameplate_id: int,
    body: AttributesBulkUpdate,
    db: Session = Depends(get_db),
):
    """
    Upsert a list of attributes for a nameplate.
    - Provide ``id`` to update an existing row.
    - Omit ``id`` to add a new attribute row.
    """
    nameplate = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        raise HTTPException(status_code=404, detail="Nameplate not found")

    for entry in body.updates:
        if entry.id is not None:
            attr = db.get(NameplateAttribute, entry.id)
            if attr is None or attr.nameplate_id != nameplate_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"Attribute id={entry.id} not found on this nameplate",
                )
            attr.attribute_name = entry.attribute_name
            attr.attribute_value = entry.attribute_value
        else:
            db.add(
                NameplateAttribute(
                    nameplate_id=nameplate_id,
                    attribute_name=entry.attribute_name,
                    attribute_value=entry.attribute_value,
                )
            )

    db.commit()
    db.refresh(nameplate)
    return [AttributeOut.model_validate(a) for a in nameplate.attributes]


@router.delete("/{nameplate_id}/attributes/{attribute_id}", status_code=204)
def delete_attribute(
    nameplate_id: int,
    attribute_id: int,
    db: Session = Depends(get_db),
):
    attr = db.get(NameplateAttribute, attribute_id)
    if attr is None or attr.nameplate_id != nameplate_id:
        raise HTTPException(status_code=404, detail="Attribute not found")
    db.delete(attr)
    db.commit()


@router.delete("/{nameplate_id}", status_code=204)
def delete_nameplate(nameplate_id: int, db: Session = Depends(get_db)):
    nameplate = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        raise HTTPException(status_code=404, detail="Nameplate not found")
    db.delete(nameplate)
    db.commit()


# ── Reprocess ─────────────────────────────────────────────────────────────────

@router.post("/{nameplate_id}/reprocess", response_model=UploadResponse)
def reprocess_nameplate(
    nameplate_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    nameplate = db.get(Nameplate, nameplate_id)
    if nameplate is None:
        raise HTTPException(status_code=404, detail="Nameplate not found")

    nameplate.status = "pending"
    nameplate.error_message = None
    db.commit()

    background_tasks.add_task(process_nameplate, nameplate.id, db)
    return UploadResponse(
        id=nameplate.id,
        status="pending",
        message="Reprocessing started.",
    )
