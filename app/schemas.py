from datetime import datetime
from pydantic import BaseModel, field_validator


# ── Attributes ────────────────────────────────────────────────────────────────

class AttributeOut(BaseModel):
    id: int
    attribute_name: str
    attribute_value: str | None
    confidence: float | None

    model_config = {"from_attributes": True}


class AttributeUpdate(BaseModel):
    attribute_name: str | None = None
    attribute_value: str | None = None


class AttributePatch(BaseModel):
    attributes: list[AttributeUpdate]

    @field_validator("attributes")
    @classmethod
    def not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("attributes list must not be empty")
        return v


class AttributeCreate(BaseModel):
    attribute_name: str
    attribute_value: str | None = None
    confidence: float | None = None


# ── Nameplates ─────────────────────────────────────────────────────────────────

class NameplateOut(BaseModel):
    id: int
    filename: str
    uploaded_at: datetime
    status: str
    error_message: str | None
    ocr_raw_text: str | None
    attributes: list[AttributeOut] = []

    model_config = {"from_attributes": True}


class NameplateSummary(BaseModel):
    id: int
    filename: str
    uploaded_at: datetime
    status: str
    error_message: str | None

    model_config = {"from_attributes": True}


class NameplateListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[NameplateSummary]


class UploadResponse(BaseModel):
    id: int
    status: str
    message: str


# ── Attribute patch (per-nameplate bulk update) ───────────────────────────────

class AttributesBulkUpdate(BaseModel):
    """
    List of attribute objects.  Each entry must have an 'id' (existing row)
    or just 'attribute_name' (to add a new one).  Set attribute_value=null to clear.
    """

    class Entry(BaseModel):
        id: int | None = None
        attribute_name: str
        attribute_value: str | None = None

    updates: list[Entry]
