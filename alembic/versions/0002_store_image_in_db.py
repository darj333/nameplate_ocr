"""store uploaded image bytes in the database

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both nullable: existing rows keep working (served from file_path until
    # re-uploaded), new uploads populate them.
    op.add_column("nameplates", sa.Column("image_data", sa.LargeBinary(), nullable=True))
    op.add_column("nameplates", sa.Column("image_mime", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("nameplates", "image_mime")
    op.drop_column("nameplates", "image_data")
