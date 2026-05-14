"""invoice table — uploaded PDF + extraction state + canonical data

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("pdf_object_key", sa.String(length=255), nullable=False),
        sa.Column("pdf_size_bytes", sa.Integer(), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255)),
        sa.Column("canonical_data", postgresql.JSONB()),
        sa.Column("extraction_path", sa.String(length=32)),
        sa.Column("extraction_model", sa.String(length=64)),
        sa.Column("extraction_version", sa.String(length=16)),
        sa.Column("overall_confidence", sa.Float()),
        sa.Column("extracted_at", sa.DateTime(timezone=True)),
        sa.Column("extraction_error", sa.String(length=1024)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_invoices_org_id", "invoices", ["org_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_pdf_object_key", "invoices", ["pdf_object_key"], unique=True)
    op.create_index("ix_invoices_pdf_sha256", "invoices", ["pdf_sha256"])
    op.create_index("ix_invoices_deleted_at", "invoices", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("invoices")
