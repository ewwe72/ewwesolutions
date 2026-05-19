#!/usr/bin/env bash
# Daily Postgres backup → ~/backups/ + Discord report. Runs at 03:00 UTC.
#
# pg_dump runs inside the `postgres` container (so we don't need a host-side
# psql client). Output streamed to gzip on the host. Rotation: keep last 14.
#
# Smoke-test:  ./backup_verifier.sh
# Dry-run:     DRY_RUN=1 ./backup_verifier.sh

set -euo pipefail

INVOICE_IDP_DIR="${INVOICE_IDP_DIR:-$HOME/playspace/random/invoice_idp}"
COMPOSE_FILE="${COMPOSE_FILE:-$INVOICE_IDP_DIR/docker-compose.vm.yml}"
ENV_FILE="${ENV_FILE:-$INVOICE_IDP_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"

# Surgical extraction of just BIGGA — `source .env` blows up when other
# values contain unquoted spaces (e.g. Gmail app passwords).
BIGGA=$(grep -E '^BIGGA=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//')
: "${BIGGA:?BIGGA Discord webhook not set in $ENV_FILE}"

mkdir -p "$BACKUP_DIR"

STAMP=$(date -u '+%Y%m%d_%H%M')
OUT="$BACKUP_DIR/invoice_idp_${STAMP}.sql.gz"

post_discord() {
    local content="$1"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        echo "===== DRY RUN ====="; printf '%s\n' "$content"; return 0
    fi
    curl -fsS -X POST -H 'Content-Type: application/json' \
        --data "$(jq -nc --arg content "$content" '{content: $content}')" \
        "$BIGGA" > /dev/null
}

post_failure() {
    post_discord "🚨 **Faktomat backup FAILED** — $(date -u '+%Y-%m-%d %H:%M UTC')
\`\`\`
$1
\`\`\`"
    exit 1
}

trap 'post_failure "backup_verifier.sh: line $LINENO failed (set -e)"' ERR

# In dry-run mode just preview the message we *would* post and exit.
# Don't pg_dump (it'd produce a real artefact in $BACKUP_DIR).
if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "===== DRY RUN ====="
    echo "would dump: docker compose -f $COMPOSE_FILE exec -T postgres pg_dump ... | gzip -c > $OUT"
    echo "would post backup-OK message to BIGGA webhook"
    exit 0
fi

START_TS=$(date +%s)

# Dump inside the container; pipe through gzip on host.
if ! docker compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_dump -U invoice_idp --clean --if-exists invoice_idp \
        2> /tmp/pg_dump.$$.err | gzip -c > "$OUT.tmp"; then
    post_failure "pg_dump exit non-zero. stderr:
$(head -20 /tmp/pg_dump.$$.err)"
fi

mv "$OUT.tmp" "$OUT"
rm -f /tmp/pg_dump.$$.err

SIZE_BYTES=$(stat -c '%s' "$OUT")
SIZE_HUMAN=$(numfmt --to=iec --suffix=B "$SIZE_BYTES")
ELAPSED=$(( $(date +%s) - START_TS ))

# Sanity: if backup < 1KB the dump probably failed silently.
if [ "$SIZE_BYTES" -lt 1024 ]; then
    post_failure "backup suspiciously small: $SIZE_HUMAN ($SIZE_BYTES bytes)"
fi

# Rotate: delete backups older than RETAIN_DAYS.
ROTATED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'invoice_idp_*.sql.gz' \
             -type f -mtime +"$RETAIN_DAYS" -print -delete | wc -l)

TOTAL_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name 'invoice_idp_*.sql.gz' -type f | wc -l)

trap - ERR

post_discord "💾 **Faktomat backup OK** — $(date -u '+%Y-%m-%d %H:%M UTC')
• File: \`invoice_idp_${STAMP}.sql.gz\`
• Size: $SIZE_HUMAN
• Took: ${ELAPSED}s
• Retained: $TOTAL_COUNT files (rotated $ROTATED older than ${RETAIN_DAYS}d)"

echo "backup_verifier: $OUT ($SIZE_HUMAN, ${ELAPSED}s, rotated $ROTATED)"
