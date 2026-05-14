"""Phase 6 billing — phone verification on users, credit balance on orgs.

Adds:
- `users.phone_number` (E.164) + `phone_verified_at` + token columns
  for the Twilio Verify flow (mirrors email verification).
- `orgs.credit_balance_grosze` (int) for the prepaid PAYG model. One
  successful extraction debits `settings.invoice_price_grosze` (default
  50 groszy = 0,50 PLN). Top-ups via Stripe Checkout credit it.
- `orgs.stripe_customer_id_unique` constraint — the column already
  exists from migration 0001; this adds the uniqueness only.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("phone_number", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_verification_sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "orgs",
        sa.Column(
            "credit_balance_grosze",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # The column was added in 0001 baseline but without uniqueness — add
    # it now so the Stripe webhook can safely upsert by customer_id.
    op.create_unique_constraint(
        "uq_orgs_stripe_customer_id",
        "orgs",
        ["stripe_customer_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_orgs_stripe_customer_id", "orgs", type_="unique")
    op.drop_column("orgs", "credit_balance_grosze")
    op.drop_column("users", "phone_verification_sent_at")
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "phone_number")
