#!/usr/bin/env bash
# Install (or re-install) the Faktomat agent + studio systemd units.
#
# Idempotent: copies units to /etc/systemd/system, reloads, and enables
# + starts both *.timer (periodic agents) and *.service (long-running
# static services). Safe to re-run after `git pull`.
#
# Requires sudo (systemd unit files have to live in /etc/systemd/system).

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/systemd" && pwd)"
DEST_DIR=/etc/systemd/system

# All unit files (both .timer and the matching .service backing each
# timer, plus standalone long-running services).
UNITS=(
    faktomat-daily-digest.service              faktomat-daily-digest.timer
    faktomat-backup.service                    faktomat-backup.timer
    faktomat-ultrareview-reminder.service      faktomat-ultrareview-reminder.timer
    faktomat-alpaca-snapshot.service           faktomat-alpaca-snapshot.timer
    faktomat-stripe-rotation-reminder.service  faktomat-stripe-rotation-reminder.timer
    faktomat-extraction-alerter.service        faktomat-extraction-alerter.timer
    ewwesolutions-studio.service
)

# Periodic agents — enabled via their timers.
TIMERS=(
    faktomat-daily-digest.timer
    faktomat-backup.timer
    faktomat-ultrareview-reminder.timer
    faktomat-alpaca-snapshot.timer
    faktomat-stripe-rotation-reminder.timer
    faktomat-extraction-alerter.timer
)

# Long-running services — enabled directly (no timer).
SERVICES=(
    ewwesolutions-studio.service
)

echo "==> Copying unit files: $SRC_DIR → $DEST_DIR"
for unit in "${UNITS[@]}"; do
    sudo install -o root -g root -m 0644 "$SRC_DIR/$unit" "$DEST_DIR/$unit"
    echo "  • $unit"
done

echo "==> systemctl daemon-reload"
sudo systemctl daemon-reload

echo "==> Enabling + starting timers"
for t in "${TIMERS[@]}"; do
    sudo systemctl enable --now "$t"
    echo "  • $t"
done

echo "==> Enabling + (re)starting services"
for s in "${SERVICES[@]}"; do
    sudo systemctl enable "$s"
    # restart (not start) so re-runs after a code change pick up the
    # new working tree without manual intervention. systemd is OK with
    # restarting a not-yet-running service — it just starts it.
    sudo systemctl restart "$s"
    echo "  • $s"
done

echo
echo "==> Active timers:"
systemctl list-timers --all 'faktomat-*' --no-pager
echo
echo "==> Static services:"
systemctl --no-pager status "${SERVICES[@]}" | head -20
