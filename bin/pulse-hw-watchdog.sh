#!/usr/bin/env bash
# Monitor kernel messages for fatal hardware failures and trigger a safe reboot.
#
# Watches dmesg for patterns that indicate unrecoverable hardware states where
# the only fix is a reboot.  New patterns can be added to the FATAL_PATTERNS
# array below.
set -euo pipefail

LOG_TAG="pulse-hw-watchdog"

log_msg() {
  local msg="$1"
  if command -v logger >/dev/null 2>&1; then
    logger -t "$LOG_TAG" "$msg"
  fi
  echo "$msg" >&2
}

# ── Fatal patterns (trigger safe reboot) ────────────────────────────────────
# Each entry: "pattern|reboot-reason"
# pattern   — substring matched against kernel messages (case-sensitive)
# reason    — short tag passed to safe-reboot.sh for the reboot log
FATAL_PATTERNS=(
  # USB host controller death — all devices on the bus go permanently offline
  "HC died|xhci-controller-died"
  "host controller not responding, assume dead|xhci-controller-died"
  # WiFi firmware crash — network permanently offline until reboot
  "brcmfmac: brcmf_fw_crashed|wifi-firmware-crashed"
  "brcmfmac: Firmware has halted or crashed|wifi-firmware-crashed"
  # GPU/display hang — kiosk display frozen
  "drm/scheduler: job timedout|gpu-hang"
  "gpu sched timeout|gpu-hang"
)

# ── Warning patterns (log only, no reboot) ──────────────────────────────────
# These indicate hardware issues worth tracking but rebooting won't fix.
WARN_PATTERNS=(
  "Under-voltage detected"
  "I/O error, dev mmcblk0"
)

# ── Grace period ────────────────────────────────────────────────────────────
# Let the system settle after boot before monitoring.
sleep 60

log_msg "Hardware watchdog started, monitoring ${#FATAL_PATTERNS[@]} fatal and ${#WARN_PATTERNS[@]} warning pattern(s)"

# ── Main loop ───────────────────────────────────────────────────────────────
dmesg --follow --level warn,err,crit,alert,emerg 2>/dev/null | while IFS= read -r line; do
  for entry in "${FATAL_PATTERNS[@]}"; do
    pattern="${entry%%|*}"
    reason="${entry##*|}"
    if [[ "$line" == *"$pattern"* ]]; then
      log_msg "FATAL hardware failure detected (reason=$reason): $line"
      log_msg "Triggering safe reboot"
      exec /opt/pulse-os/bin/safe-reboot.sh "$reason"
    fi
  done
  for pattern in "${WARN_PATTERNS[@]}"; do
    if [[ "$line" == *"$pattern"* ]]; then
      log_msg "WARNING hardware issue detected: $line"
    fi
  done
done
