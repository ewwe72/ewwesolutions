"""invoice review columns — user_reviewed_at + last_correction_at

Phase 4 chunk 3b. The review page lets the operator hand-edit extracted
fields; we stamp `last_correction_at` on every save and `user_reviewed_at`
on the first save so exports can later gate on "operator has signed off".

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("user_reviewed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "invoices",
        sa.Column("last_correction_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("invoices", "last_correction_at")
    op.drop_column("invoices", "user_reviewed_at")
