#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="/opt/pulse-os/pulse.conf"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

LOG_TAG="pulse-safe-reboot"
STATE_DIR="/run/pulse"
HISTORY_FILE="$STATE_DIR/reboot-history.log"
MIN_UPTIME=${PULSE_REBOOT_MIN_UPTIME_SECONDS:-300}
WINDOW=${PULSE_REBOOT_WINDOW_SECONDS:-900}
MAX_COUNT=${PULSE_REBOOT_MAX_COUNT:-3}
REASON=${1:-"unspecified"}

mkdir -p "$STATE_DIR"
touch "$HISTORY_FILE"

# Helper to log to both logger and stderr
log_msg() {
  local msg="$1"
  if command -v logger >/dev/null 2>&1; then
    logger -t "$LOG_TAG" "$msg"
  fi
  echo "$msg" >&2
}

uptime_seconds=$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)
if (( uptime_seconds < MIN_UPTIME )); then
  log_msg "Skipping reboot (uptime ${uptime_seconds}s < ${MIN_UPTIME}s). Reason: ${REASON}"
  exit 0
}

now=$(date +%s)
cutoff=$((now - WINDOW))
tmp_file=$(mktemp)
awk -v limit="$cutoff" '$1 >= limit' "$HISTORY_FILE" > "$tmp_file"
mv "$tmp_file" "$HISTORY_FILE"

recent_count=$(wc -l < "$HISTORY_FILE")
if (( recent_count >= MAX_COUNT )); then
  log_msg "Skipping reboot (>=${MAX_COUNT} attempts within last ${WINDOW}s). Reason: ${REASON}"
  exit 0
fi

echo "$now $REASON" >> "$HISTORY_FILE"
log_msg "Rebooting (reason: ${REASON})"
exec /sbin/reboot

