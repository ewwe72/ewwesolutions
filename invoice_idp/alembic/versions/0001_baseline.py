"""baseline — orgs, users, usage, audit_events

Revision ID: 0001
Revises:
Create Date: 2026-05-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("nip", sa.String(length=10)),
        sa.Column("regon", sa.String(length=14)),
        sa.Column("kod_urzedu", sa.String(length=10)),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_orgs_nip", "orgs", ["nip"])
    op.create_index("ix_orgs_stripe_customer_id", "orgs", ["stripe_customer_id"])
    op.create_index("ix_orgs_deleted_at", "orgs", ["deleted_at"])

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_verification_token", sa.String(length=64)),
        sa.Column("email_verification_sent_at", sa.DateTime(timezone=True)),
        sa.Column("password_reset_token", sa.String(length=64)),
        sa.Column("password_reset_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_email_verification_token", "users", ["email_verification_token"])
    op.create_index("ix_users_password_reset_token", "users", ["password_reset_token"])
    op.create_index("ix_users_org_id", "users", ["org_id"])
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    op.create_table(
        "usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("invoices_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overage_invoices", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "year_month", name="uq_usage_org_month"),
    )
    op.create_index("ix_usage_org_id", "usage", ["org_id"])
    op.create_index("ix_usage_year_month", "usage", ["year_month"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("ip", sa.String(length=64)),
        sa.Column("user_agent", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_org_id", "audit_events", ["org_id"])
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("usage")
    op.drop_table("users")
    op.drop_table("orgs")
