"""Importing this package registers every SQLAlchemy model with `Base.metadata`.

Any process (web app, arq worker, alembic, ad-hoc script) that touches
even one model gets the full metadata transitively, so cross-table FKs
resolve no matter which submodule was imported first.

The Pydantic `CanonicalInvoice` in `invoice.py` is intentionally NOT
re-exported here — it's a DTO contract, not a DB table, and importing
it would force Pydantic resolution at unrelated entry points.
"""

from src.models import audit, invoice_record, org, usage, user  # noqa: F401

__all__ = ["audit", "invoice_record", "org", "usage", "user"]
