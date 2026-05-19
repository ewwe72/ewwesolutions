#!/usr/bin/env bash
# Monthly Stripe LIVE secret rotation reminder → Discord.
#
# Rotation is operator-side: Stripe Dashboard → Developers → API keys
# → roll the live secret + update STRIPE_SECRET_KEY in
# invoice_idp/.env on the VM → `docker compose -f docker-compose.vm.yml
# restart app worker`. Same for webhook secret if rolling that too.
#
# This script just nudges. Fires on the 1st of every month at 10:00 UTC.
#
# Smoke-test: ./stripe_rotation_reminder.sh

set -euo pipefail

INVOICE_IDP_DIR="${INVOICE_IDP_DIR:-$HOME/playspace/random/invoice_idp}"
ENV_FILE="${ENV_FILE:-$INVOICE_IDP_DIR/.env}"

BIGGA=$(grep -E '^BIGGA=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
: "${BIGGA:?BIGGA Discord webhook not set in $ENV_FILE}"

MSG=$(cat <<'EOF'
🔑 **Stripe secret rotation reminder** — monthly cadence

Time to roll the **live** Stripe secrets. Steps:

1. Stripe Dashboard → Developers → API keys → "Roll" on the live secret key (`sk_live_...`)
2. Update `invoice_idp/.env` on the VM: `STRIPE_SECRET_KEY=<new-value>`
3. (Optional, same cadence) Webhooks → endpoint signing secret → roll → update `STRIPE_WEBHOOK_SECRET`
4. Restart the worker + app so the new value loads:
   ```
   cd ~/playspace/random/invoice_idp
   docker compose -f docker-compose.vm.yml restart app worker
   ```
5. Test a 0.50 PLN top-up end-to-end to confirm webhook signature still verifies.

If you skip a month, that's fine — no compliance gun-to-the-head while there are no clients. But the longer between rotations, the bigger the blast radius if something exfiltrates the .env.
EOF
)

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "===== DRY RUN ====="; printf '%s\n' "$MSG"; exit 0
fi

curl -fsS -X POST -H 'Content-Type: application/json' \
    --data "$(jq -nc --arg content "$MSG" '{content: $content}')" \
    "$BIGGA" > /dev/null

echo "stripe_rotation_reminder: posted"
