from datetime import datetime, timezone
from sqlalchemy import Integer, String, Text, Float, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Nameplate(Base):
    __tablename__ = "nameplates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ocr_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    attributes: Mapped[list["NameplateAttribute"]] = relationship(
        "NameplateAttribute", back_populates="nameplate", cascade="all, delete-orphan"
    )


class NameplateAttribute(Base):
    __tablename__ = "nameplate_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nameplate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nameplates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attribute_name: Mapped[str] = mapped_column(Text, nullable=False)
    attribute_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    nameplate: Mapped["Nameplate"] = relationship("Nameplate", back_populates="attributes")
