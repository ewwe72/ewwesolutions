#!/usr/bin/env bash
# Per-event extraction alerter → Discord. Fires every 5 minutes.
#
# Polls audit_events for new entries since the last successful run and
# posts one short Discord message per event via the BIGGA webhook in
# invoice_idp/.env. Complements the daily_digest (roll-up) with
# real-time ops awareness — during early customer ramp each invoice
# matters.
#
# Events watched:
#   - invoice.extracted          → "✅ Ekstrakcja OK | <id> | conf X.XX | <path>"
#   - invoice.extraction_failed  → "❌ Ekstrakcja FAIL | <id> | <error_raw first line>"
#   - billing.topup_credited     → "💰 Wpłata | <amount> PLN | org <8-char>"
#
# State is a single ISO timestamp written to STATE_FILE. On first run
# the cursor is initialised to `now()` so the operator is NOT spammed
# with historical events when the agent is first enabled.
#
# Smoke-test:                 ./extraction_alerter.sh
# Dry-run (no Discord post):  DRY_RUN=1 ./extraction_alerter.sh
# Reset state (re-process everything from epoch):
#                             rm -f ~/.faktomat-extraction-alerter-cursor

set -euo pipefail

INVOICE_IDP_DIR="${INVOICE_IDP_DIR:-$HOME/playspace/random/invoice_idp}"
COMPOSE_FILE="${COMPOSE_FILE:-$INVOICE_IDP_DIR/docker-compose.vm.yml}"
ENV_FILE="${ENV_FILE:-$INVOICE_IDP_DIR/.env}"
STATE_FILE="${STATE_FILE:-$HOME/.faktomat-extraction-alerter-cursor}"

# Hard cap per fire: if a burst happens (worker thrash, mass re-extract),
# we want bounded Discord noise. Events past the cap stay in the queue
# and get picked up next fire — operator can also see them via the next
# daily digest.
MAX_EVENTS_PER_FIRE="${MAX_EVENTS_PER_FIRE:-10}"
# Force integer — guards against MAX_EVENTS_PER_FIRE='10; DROP TABLE …'
# style SQL injection via env vars. Same idea as parametrised queries
# but we don't have those in a heredoc-driven shell agent.
if ! [[ "$MAX_EVENTS_PER_FIRE" =~ ^[0-9]+$ ]]; then
    echo "extraction_alerter: MAX_EVENTS_PER_FIRE must be a positive integer, got: $MAX_EVENTS_PER_FIRE" >&2
    exit 2
fi

# Hard timeout on every Discord call. Without this a hung webhook
# wedges the whole run until the next systemd timer fires.
CURL_MAX_TIME="${CURL_MAX_TIME:-10}"

# Surgical BIGGA extraction — `source .env` blows up when other values
# contain unquoted spaces (Gmail app password etc).
BIGGA=$(grep -E '^BIGGA=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
: "${BIGGA:?BIGGA Discord webhook not set in $ENV_FILE}"

psql() {
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U invoice_idp -d invoice_idp -A -t "$@"
}

# First-run init: anchor the cursor at `now()` so we don't backfill
# history into Discord. Subsequent runs read the prior cursor.
if [ ! -f "$STATE_FILE" ]; then
    # Strip leading/trailing whitespace only (don't use `tr -d ' '` —
    # Postgres prints `YYYY-MM-DD HH:MM:SS.us` and we need that
    # internal space to survive). `xargs` would also work but chokes
    # on unmatched quotes, which obscures errors on a malformed
    # STATE_FILE — sed is unambiguous.
    NOW=$(psql -c "SELECT now() AT TIME ZONE 'UTC';" | head -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    echo "$NOW" > "$STATE_FILE"
    echo "extraction_alerter: initialised cursor to $NOW (first run)"
    exit 0
fi

CURSOR=$(head -1 "$STATE_FILE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
: "${CURSOR:?STATE_FILE is empty — delete it to reinitialise}"

# Defensive validation — the cursor flows into a heredoc SQL string
# below. STATE_FILE lives in $HOME (single-user VM) so an attacker
# would need shell access already, but cheap defense in depth: only
# allow ISO-ish timestamps. Anything else => bail loudly.
if ! [[ "$CURSOR" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[T\ ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?([+-][0-9]{2}:?[0-9]{2}|Z)?$ ]]; then
    echo "extraction_alerter: STATE_FILE content does not look like an ISO timestamp: $CURSOR" >&2
    echo "Delete $STATE_FILE to reinitialise (will anchor at now() and skip historical events)" >&2
    exit 2
fi

# Pull new events. Order ASC so the cursor moves forward monotonically.
# Columns: created_at | action | invoice_short | confidence | path | error | amount_grosze | org_short
#
# Separator note: `|` (not tab). Bash's `read` collapses sequences of
# whitespace IFS characters into a single delimiter, so a tab-separated
# row with empty middle columns like `a\tb\t\t\tc` would be parsed as 3
# fields, not 5. With IFS='|' bash preserves empty fields correctly.
# Pipes inside error messages get replaced with `¦` so they don't
# corrupt the column count.
ROWS=$(psql -F '|' <<SQL
SELECT
    to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
    action,
    COALESCE(substring(payload->>'invoice_id', 1, 8), ''),
    COALESCE(to_char((payload->>'confidence')::numeric, 'FM0.00'), ''),
    COALESCE(payload->>'path', ''),
    COALESCE(replace(substring(COALESCE(payload->>'error_raw', payload->>'error'), 1, 80), '|', '¦'), ''),
    COALESCE(payload->>'amount_grosze', ''),
    COALESCE(substring(org_id::text, 1, 8), '')
  FROM audit_events
 WHERE created_at > '$CURSOR'::timestamptz
   AND action IN ('invoice.extracted', 'invoice.extraction_failed', 'billing.topup_credited')
 ORDER BY created_at ASC
 LIMIT $MAX_EVENTS_PER_FIRE;
SQL
)

if [ -z "$ROWS" ]; then
    echo "extraction_alerter: no new events since $CURSOR"
    exit 0
fi

post_to_discord() {
    local content="$1"
    local http_code
    http_code=$(curl -sS --max-time "$CURL_MAX_TIME" \
        -o /tmp/discord-alerter-resp.$$ -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json' \
        --data "$(jq -nc --arg content "$content" '{content: $content}')" \
        "$BIGGA")
    if [ "$http_code" != "204" ] && [ "$http_code" != "200" ]; then
        echo "Discord webhook returned $http_code" >&2
        cat /tmp/discord-alerter-resp.$$ >&2
        rm -f /tmp/discord-alerter-resp.$$
        return 1
    fi
    rm -f /tmp/discord-alerter-resp.$$
    return 0
}

format_event() {
    local action="$1" inv_short="$2" conf="$3" path="$4" err="$5" amt_grosze="$6" org_short="$7"
    case "$action" in
        invoice.extracted)
            local conf_str="conf $conf"
            [ -z "$conf" ] && conf_str="conf —"
            local path_str=""
            [ -n "$path" ] && path_str=" | $path"
            printf '✅ Ekstrakcja OK | `%s` | %s%s' "$inv_short" "$conf_str" "$path_str"
            ;;
        invoice.extraction_failed)
            local err_str="$err"
            [ -z "$err_str" ] && err_str="(brak detalu)"
            printf '❌ Ekstrakcja FAIL | `%s` | %s' "$inv_short" "$err_str"
            ;;
        billing.topup_credited)
            # grosze → PLN with comma decimal.
            local amt_pln
            amt_pln=$(awk -v g="${amt_grosze:-0}" 'BEGIN { printf "%.2f", g / 100 }' | tr '.' ',')
            printf '💰 Wpłata | %s PLN | org `%s`' "$amt_pln" "$org_short"
            ;;
        *)
            # Unknown action — surface as-is so the operator sees it
            # rather than silently dropping. Shouldn't happen with the
            # SQL filter above.
            printf 'ℹ️ %s | `%s`' "$action" "$inv_short"
            ;;
    esac
}

POSTED=0
LAST_TS="$CURSOR"

# IFS=|  — non-whitespace, so `read` keeps empty fields between
# delimiters (see "Separator note" on the SQL above for the why).
while IFS='|' read -r created_at action inv_short conf path err amt_grosze org_short; do
    [ -z "$created_at" ] && continue   # blank line at end of psql output
    msg=$(format_event "$action" "$inv_short" "$conf" "$path" "$err" "$amt_grosze" "$org_short")

    if [ "${DRY_RUN:-0}" = "1" ]; then
        echo "===== DRY RUN [$created_at] ====="
        printf '%s\n' "$msg"
    else
        if ! post_to_discord "$msg"; then
            echo "extraction_alerter: post failed at $created_at; not advancing cursor" >&2
            # Leave cursor at LAST_TS so the next fire retries from
            # this event forward. Don't dupe-post the ones already
            # delivered.
            echo "$LAST_TS" > "$STATE_FILE"
            exit 1
        fi
        # Stay under Discord's 5 req/sec per-webhook rate.
        sleep 0.25
    fi

    LAST_TS="$created_at"
    POSTED=$((POSTED + 1))
done <<<"$ROWS"

if [ "${DRY_RUN:-0}" != "1" ]; then
    echo "$LAST_TS" > "$STATE_FILE"
fi

echo "extraction_alerter: posted $POSTED event(s), cursor=$LAST_TS"

# If we hit the per-fire cap there are probably more events queued
# behind. Surface that as a separate Discord ping so the operator
# knows the daily digest is the better surface for the rest.
if [ "$POSTED" -eq "$MAX_EVENTS_PER_FIRE" ] && [ "${DRY_RUN:-0}" != "1" ]; then
    post_to_discord "⚠️ Extraction alerter hit per-fire cap ($MAX_EVENTS_PER_FIRE). Pozostałe wydarzenia trafią w kolejny fire lub do daily digest." || true
fi
