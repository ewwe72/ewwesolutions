#!/usr/bin/env bash
# /ultrareview reminder → Discord. Runs every 10 days (3×/month cap).
#
# /ultrareview is user-triggered and billed. This script just pings the
# operator to remember to run it manually; it does NOT trigger the review
# itself.
#
# Smoke-test:  ./ultrareview_reminder.sh

set -euo pipefail

INVOICE_IDP_DIR="${INVOICE_IDP_DIR:-$HOME/playspace/random/invoice_idp}"
ENV_FILE="${ENV_FILE:-$INVOICE_IDP_DIR/.env}"

# Surgical extraction of just BIGGA — `source .env` blows up when other
# values contain unquoted spaces (e.g. Gmail app passwords).
BIGGA=$(grep -E '^BIGGA=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
: "${BIGGA:?BIGGA Discord webhook not set in $ENV_FILE}"

# Show last 10 commits since the last reminder so operator has context for
# what /ultrareview would actually be reviewing.
RECENT_COMMITS=$(cd "$HOME/playspace/random" && git log --oneline -10 main 2>/dev/null | sed 's/^/• /')

MSG=$(cat <<EOF
🔍 **/ultrareview reminder** — $(date -u '+%Y-%m-%d')

Time for one of the three monthly reviews. Run from this repo:
\`\`\`
/ultrareview
\`\`\`

Recent commits on \`main\` (review target):
$RECENT_COMMITS
EOF
)

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "===== DRY RUN ====="; printf '%s\n' "$MSG"; exit 0
fi

curl -fsS -X POST -H 'Content-Type: application/json' \
    --data "$(jq -nc --arg content "$MSG" '{content: $content}')" \
    "$BIGGA" > /dev/null

echo "ultrareview_reminder: posted"
