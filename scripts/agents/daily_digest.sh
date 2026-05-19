#!/usr/bin/env bash
# Daily Faktomat digest → Discord. Runs at 09:00 UTC (~11:00 PL).
# Pulls stats from audit_events + invoices for the last 24h, then posts
# a single Discord message via the BIGGA webhook in invoice_idp/.env.
#
# Smoke-test:  ./daily_digest.sh
# Dry-run (no Discord post): DRY_RUN=1 ./daily_digest.sh

set -euo pipefail

INVOICE_IDP_DIR="${INVOICE_IDP_DIR:-$HOME/playspace/random/invoice_idp}"
COMPOSE_FILE="${COMPOSE_FILE:-$INVOICE_IDP_DIR/docker-compose.vm.yml}"
ENV_FILE="${ENV_FILE:-$INVOICE_IDP_DIR/.env}"

# Surgical extraction of just BIGGA — `source .env` blows up when other
# values contain unquoted spaces (e.g. Gmail app passwords).
BIGGA=$(grep -E '^BIGGA=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
: "${BIGGA:?BIGGA Discord webhook not set in $ENV_FILE}"

psql() {
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U invoice_idp -d invoice_idp -A -t "$@"
}

# One round-trip query → pipe-separated row.
STATS=$(psql -F '|' <<'SQL'
SELECT
  (SELECT count(*) FROM users WHERE created_at > now() - interval '24 hours' AND deleted_at IS NULL),
  (SELECT count(*) FROM users WHERE email_verified AND email_verification_sent_at > now() - interval '24 hours'),
  (SELECT count(*) FROM invoices WHERE created_at > now() - interval '24 hours' AND deleted_at IS NULL),
  (SELECT count(*) FROM invoices WHERE extracted_at > now() - interval '24 hours' AND status = 'completed'),
  (SELECT count(*) FROM invoices WHERE updated_at > now() - interval '24 hours' AND status = 'failed'),
  COALESCE(to_char(
      (SELECT avg(overall_confidence) FROM invoices
       WHERE extracted_at > now() - interval '24 hours' AND status = 'completed'),
      'FM0.00'), 'n/a'),
  (SELECT count(*) FROM invoices
   WHERE extracted_at > now() - interval '24 hours' AND status = 'completed' AND overall_confidence < 0.75),
  (SELECT count(*) FROM audit_events
   WHERE created_at > now() - interval '24 hours' AND action = 'billing.topup_credited'),
  COALESCE((SELECT sum((payload->>'amount_grosze')::int) FROM audit_events
   WHERE created_at > now() - interval '24 hours' AND action = 'billing.topup_credited'), 0),
  (SELECT count(*) FROM audit_events
   WHERE created_at > now() - interval '24 hours' AND action = 'invoice.exported');
SQL
)

IFS='|' read -r SIGNUPS EMAIL_VERIFIED UPLOADS EXTRACTIONS FAILURES \
                  AVG_CONF LOW_CONF TOPUPS TOPUP_GROSZE EXPORTS <<<"$STATS"

# Topup PLN, with comma decimal.
TOPUP_PLN=$(awk -v g="${TOPUP_GROSZE:-0}" 'BEGIN { printf "%.2f", g / 100 }' | tr '.' ',')

# Low-confidence invoice IDs (max 10, short uuid).
LOW_CONF_IDS=$(psql <<'SQL' | head -10 | awk '{ printf "• `%s` (%s, %s)\n", substr($1,1,8), $2, $3 }'
SELECT id::text, to_char(overall_confidence, 'FM0.00') AS conf, original_filename
  FROM invoices
 WHERE extracted_at > now() - interval '24 hours'
   AND status = 'completed'
   AND overall_confidence < 0.75
 ORDER BY overall_confidence ASC;
SQL
)

# Recent failed extractions (max 5).
FAILED_IDS=$(psql <<'SQL' | head -5 | awk -F '|' '{ printf "• `%s` — %s\n", substr($1,1,8), $2 }'
SELECT id::text, COALESCE(substring(extraction_error, 1, 80), 'no error msg')
  FROM invoices
 WHERE updated_at > now() - interval '24 hours' AND status = 'failed'
 ORDER BY updated_at DESC;
SQL
)

# Compose the message.
MSG_HEADER=$(cat <<EOF
🌅 **Faktomat daily digest** — $(date -u '+%Y-%m-%d')
*last 24h, host=$(hostname)*

**Users**
  • Signups: $SIGNUPS  • Email-verified: $EMAIL_VERIFIED

**Invoices**
  • Uploads: $UPLOADS  • Extractions: $EXTRACTIONS (avg conf $AVG_CONF)
  • Failures: $FAILURES  • Low-conf (<0.75): $LOW_CONF
  • Exports: $EXPORTS

**Billing**
  • Top-ups credited: $TOPUPS (sum: $TOPUP_PLN PLN)
EOF
)

MSG="$MSG_HEADER"

if [ -n "$LOW_CONF_IDS" ]; then
    MSG=$(printf '%s\n\n**Low-confidence invoices**\n%s' "$MSG" "$LOW_CONF_IDS")
fi
if [ -n "$FAILED_IDS" ]; then
    MSG=$(printf '%s\n\n**Failed extractions**\n%s' "$MSG" "$FAILED_IDS")
fi

# Discord 2000-char hard cap; truncate defensively.
MSG=$(printf '%s' "$MSG" | head -c 1900)

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "===== DRY RUN ====="
    printf '%s\n' "$MSG"
    exit 0
fi

HTTP_CODE=$(curl -sS -o /tmp/discord-response.$$ -w '%{http_code}' \
    -X POST -H 'Content-Type: application/json' \
    --data "$(jq -nc --arg content "$MSG" '{content: $content}')" \
    "$BIGGA")

if [ "$HTTP_CODE" != "204" ] && [ "$HTTP_CODE" != "200" ]; then
    echo "Discord webhook returned $HTTP_CODE" >&2
    cat /tmp/discord-response.$$ >&2
    rm -f /tmp/discord-response.$$
    exit 1
fi
rm -f /tmp/discord-response.$$
echo "daily_digest: posted ($HTTP_CODE)"
