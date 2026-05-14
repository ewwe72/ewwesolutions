"""One-off arq enqueue for `extract_invoice_task`.

Use when a Faktura row in the DB is stuck in `pending` or `failed` and
needs to be re-processed without waiting for the next upload. Runs
inside the worker container — Redis hostname is the compose service
name `redis`, not `localhost`.

Usage (from the project root on the host):

    docker compose exec worker python enqueue.py <invoice_id> [model]

Examples:

    docker compose exec worker python enqueue.py 61e4bf4e-b2b9-4712-ac53-9d206cb8da48
    docker compose exec worker python enqueue.py 61e4bf4e-...  claude-sonnet-4-6

If `model` is omitted the worker uses the default Haiku-first routing.
Pass `claude-sonnet-4-6` (or another id from `BEDROCK_MODEL_MAP`) to
force a specific model — same hook the "Re-ekstrakcja (Sonnet)" UI
button uses.
"""

from __future__ import annotations

import asyncio
import os
import sys

from arq import create_pool
from arq.connections import RedisSettings


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python enqueue.py <invoice_id> [force_model]", file=sys.stderr)
        sys.exit(2)

    invoice_id = sys.argv[1]
    force_model = sys.argv[2] if len(sys.argv) > 2 else None

    # Inside the worker container the Redis service hostname is `redis`.
    # If you ever run this from the host directly (e.g. `python enqueue.py …`
    # outside compose), export REDIS_HOST=localhost first.
    redis_host = os.environ.get("REDIS_HOST", "redis")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    pool = await create_pool(RedisSettings(host=redis_host, port=redis_port))

    if force_model is None:
        await pool.enqueue_job("extract_invoice_task", invoice_id)
    else:
        await pool.enqueue_job("extract_invoice_task", invoice_id, force_model)

    print(f"enqueued extract_invoice_task({invoice_id}, force_model={force_model!r})")


if __name__ == "__main__":
    asyncio.run(main())
