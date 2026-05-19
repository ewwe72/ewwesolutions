#!/usr/bin/env bash
# Alpaca paper-account snapshot → marketing/ewwesolutions/stats.json.
#
# Hard guards (in order):
#   1. Refuses if APCA_API_BASE_URL is anything other than paper-api.alpaca.
#      markets. Live URL = exit 1, no matter what keys are in the .env.
#   2. Only GET requests. No POST/PATCH/DELETE — read-only by construction.
#   3. JSON output contains equity / last_equity / position_count / asof.
#      Never any credentials, account ID, or key prefix.
#   4. Errors are logged but never echo the keys; curl prefix flag set.
#
# Smoke-test:  ./alpaca_paper_snapshot.sh
# Dry-run:     DRY_RUN=1 ./alpaca_paper_snapshot.sh

set -euo pipefail

ROOT="${ROOT:-$HOME/playspace/random}"
OUT="${OUT:-$ROOT/marketing/ewwesolutions/stats.json}"

# Discord webhook for failure pings (silent on success — we don't want
# every 30-min snapshot to spam the channel).
INVOICE_ENV="$ROOT/invoice_idp/.env"
BIGGA=$(grep -E '^BIGGA=' "$INVOICE_ENV" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//' || echo "")

post_failure() {
    local msg="$1"
    echo "$msg" >&2
    if [ -n "$BIGGA" ] && [ "${DRY_RUN:-0}" != "1" ]; then
        curl -fsS -X POST -H 'Content-Type: application/json' \
            --data "$(jq -nc --arg content "🚨 alpaca_paper_snapshot.sh: $msg" '{content: $content}')" \
            "$BIGGA" > /dev/null 2>&1 || true
    fi
    exit 1
}

# Surgical extraction — never `source .env` (operator's .envs have spaces).
read_env_var() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 1
    grep -E "^${key}=" "$file" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//'
}

# Query one Alpaca paper account → echo JSON object {equity, last_equity, positions, ok}.
# All credential handling is inside this function; nothing leaks above.
query_account() {
    local label="$1" env_file="$2"

    local base_url key_id secret_key
    base_url=$(read_env_var "$env_file" APCA_API_BASE_URL || true)
    key_id=$(read_env_var "$env_file" APCA_API_KEY_ID || true)
    secret_key=$(read_env_var "$env_file" APCA_API_SECRET_KEY || true)

    # Guard 1: paper-only enforcement. Must start with paper-api.alpaca.markets.
    # Accepts trailing /, /v2, /v2/, etc. Refuses live (api.alpaca.markets).
    case "$base_url" in
        https://paper-api.alpaca.markets|https://paper-api.alpaca.markets/*) ;;
        "")  echo "{\"label\":\"$label\",\"ok\":false,\"error\":\"missing_env\"}"; return 0 ;;
        *)   post_failure "REFUSED $label: APCA_API_BASE_URL is not paper-api (got: $base_url). Will not call live API." ;;
    esac

    if [ -z "$key_id" ] || [ -z "$secret_key" ]; then
        echo "{\"label\":\"$label\",\"ok\":false,\"error\":\"missing_keys\"}"
        return 0
    fi

    # Guard 2: read-only GET, explicit account endpoint.
    local resp http_code
    resp=$(curl -sS -o /tmp/alpaca.$$.body -w '%{http_code}' \
        -X GET \
        -H "APCA-API-KEY-ID: $key_id" \
        -H "APCA-API-SECRET-KEY: $secret_key" \
        -H "Accept: application/json" \
        "https://paper-api.alpaca.markets/v2/account" 2>/dev/null) || resp="000"
    http_code="$resp"

    if [ "$http_code" != "200" ]; then
        rm -f /tmp/alpaca.$$.body
        echo "{\"label\":\"$label\",\"ok\":false,\"error\":\"http_$http_code\"}"
        return 0
    fi

    # Guard 3: filter the response — only safe public fields.
    local body
    body=$(cat /tmp/alpaca.$$.body)
    rm -f /tmp/alpaca.$$.body

    # Get position count separately (lightweight call).
    local positions
    positions=$(curl -sS -X GET \
        -H "APCA-API-KEY-ID: $key_id" \
        -H "APCA-API-SECRET-KEY: $secret_key" \
        "https://paper-api.alpaca.markets/v2/positions" 2>/dev/null \
      | jq 'length' 2>/dev/null || echo "0")

    # Wipe local var copies — defensive, not strictly necessary.
    key_id=""; secret_key=""

    # Extract only equity + last_equity, drop account_number / id / etc.
    echo "$body" | jq -c \
        --arg label "$label" \
        --argjson positions "$positions" \
        '{
            label: $label,
            ok: true,
            equity: (.equity | tonumber),
            last_equity: (.last_equity | tonumber),
            cash: (.cash | tonumber),
            positions: $positions,
            currency: (.currency // "USD"),
            status: (.status // "unknown")
        }'
}

# ----- main -----

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "===== DRY RUN ====="
    echo "would read from: $ROOT/momentum/.env, $ROOT/crypto_momentum/.env"
    echo "would write to:  $OUT"
    exit 0
fi

MOMENTUM_JSON=$(query_account "momentum"        "$ROOT/momentum/.env")
CRYPTO_JSON=$(query_account   "crypto_momentum" "$ROOT/crypto_momentum/.env")

mkdir -p "$(dirname "$OUT")"
jq -n \
    --argjson momentum "$MOMENTUM_JSON" \
    --argjson crypto "$CRYPTO_JSON" \
    --arg asof "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    '{asof: $asof, accounts: [$momentum, $crypto]}' > "$OUT.tmp"

mv "$OUT.tmp" "$OUT"

# One-line stdout for journalctl visibility (no values, just status).
echo "alpaca_paper_snapshot: wrote $OUT ($(stat -c '%s' "$OUT") bytes)"
