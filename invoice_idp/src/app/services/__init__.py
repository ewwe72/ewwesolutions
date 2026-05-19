"""Cross-router service helpers.

This package holds small, pure functions that are shared between the
JSON API (`src/app/api/`) and the HTML web routes (`src/app/web/`)
so the two surfaces don't drift apart on validation rules. Each helper
must be UI-agnostic: it returns categorical results (or raises a
typed exception) and lets the caller pick the right copy + status code.

Keep this layer thin. Anything that needs DB session, settings, or a
FastAPI request belongs in the route module, not here.
"""
