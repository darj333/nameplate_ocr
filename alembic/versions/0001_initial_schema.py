"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nameplates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ocr_raw_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nameplates_id", "nameplates", ["id"])

    op.create_table(
        "nameplate_attributes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nameplate_id", sa.Integer(), nullable=False),
        sa.Column("attribute_name", sa.Text(), nullable=False),
        sa.Column("attribute_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["nameplate_id"], ["nameplates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nameplate_attributes_id", "nameplate_attributes", ["id"])
    op.create_index(
        "ix_nameplate_attributes_nameplate_id",
        "nameplate_attributes",
        ["nameplate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_nameplate_attributes_nameplate_id", "nameplate_attributes")
    op.drop_index("ix_nameplate_attributes_id", "nameplate_attributes")
    op.drop_table("nameplate_attributes")
    op.drop_index("ix_nameplates_id", "nameplates")
    op.drop_table("nameplates")
