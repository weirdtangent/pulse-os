#!/usr/bin/env bash
exec >>"$HOME/kiosk.log" 2>&1

# Be tolerant of hiccups (don't use -e)
case "${PULSE_KIOSK_DEBUG:-0}" in
  1|true|TRUE|yes|YES)
    set -x
    ;;
esac

source /opt/pulse-os/pulse.conf
SAFE_REBOOT="/opt/pulse-os/bin/safe-reboot.sh"

DEFAULT_WATCHDOG_URL="http://homeassistant.local:8123/static/icons/favicon.ico"
DEFAULT_WATCHDOG_INTERVAL=60
DEFAULT_WATCHDOG_LIMIT=5

set_positive_default() {
  local var_name="$1"
  local fallback="$2"
  local label="$3"
  local value="${!var_name:-}"

  if [[ -z "$value" ]]; then
    printf -v "$var_name" "%s" "$fallback"
    return
  fi

  if [[ "$value" =~ ^[0-9]+$ ]] && (( value > 0 )); then
    printf -v "$var_name" "%s" "$value"
  else
    echo "$(date) $label invalid ('$value'); using $fallback" >&2
    printf -v "$var_name" "%s" "$fallback"
  fi
}

: "${PULSE_WATCHDOG_URL:=$DEFAULT_WATCHDOG_URL}"
set_positive_default PULSE_WATCHDOG_INTERVAL "$DEFAULT_WATCHDOG_INTERVAL" "PULSE_WATCHDOG_INTERVAL"
set_positive_default PULSE_WATCHDOG_LIMIT "$DEFAULT_WATCHDOG_LIMIT" "PULSE_WATCHDOG_LIMIT"

echo "==== $(date) kiosk start (DISPLAY=${DISPLAY:-unset}) ===="

DEFAULT_URL="https://github.com/weirdTangent/pulse-os"
URL="${PULSE_URL:-$DEFAULT_URL}"

append_pulse_host_param() {
  local url="$1"
  local host="$2"
  if [[ -z "$url" || -z "$host" ]]; then
    printf '%s' "$url"
    return
  fi
  if [[ "$url" == *"?pulse_host="* || "$url" == *"&pulse_host="* ]]; then
    printf '%s' "$url"
    return
  fi
  local fragment=""
  if [[ "$url" == *"#"* ]]; then
    fragment="${url#*#}"
    url="${url%%#*}"
  fi
  local sep='?'
  [[ "$url" == *\?* ]] && sep='&'
  url="${url}${sep}pulse_host=${host}"
  if [[ -n "$fragment" ]]; then
    url="${url}#${fragment}"
  fi
  printf '%s' "$url"
}

HOSTNAME_FALLBACK="${PULSE_HOSTNAME:-}"
if [[ -z "$HOSTNAME_FALLBACK" ]]; then
  if command -v hostname >/dev/null 2>&1; then
    HOSTNAME_FALLBACK="$(hostname -s 2>/dev/null || hostname)"
  fi
  # Final guard: empty string if hostname command failed
  HOSTNAME_FALLBACK="${HOSTNAME_FALLBACK:-}"
fi

URL="$(append_pulse_host_param "$URL" "$HOSTNAME_FALLBACK")"

# Isolate Chromium temp files away from /tmp (tmpfs)
export TMPDIR="$HOME/.cache/chromium-tmp"
mkdir -p "$TMPDIR"
chmod 700 "$TMPDIR"

# Wait for X to be fully up so xrandr/xset won't fail
for i in {1..50}; do DISPLAY=:0 xrandr --query >/dev/null 2>&1 && break; sleep 0.5; done

# Find active DSI connector (DSI-1 or DSI-2)
DSI_OUT=$(DISPLAY=:0 xrandr | awk '/^DSI-[12] connected/{print $1; exit}')
[ -z "$DSI_OUT" ] && DSI_OUT="DSI-2"  # harmless fallback

# Keep the screen awake
DISPLAY=:0 xset s off     || true
DISPLAY=:0 xset -dpms     || true
DISPLAY=:0 xset s noblank || true

# reset any stale panning so the screen can size correctly
DISPLAY=:0 xrandr --output "$DSI_OUT" --panning 0x0 || true

# Match X to the rotated panel and avoid the half-black strip:
DISPLAY=:0 xrandr --fb 720x1280 || true
DISPLAY=:0 xrandr --output "$DSI_OUT" --mode 720x1280 --rotate right --panning 0x0 || true

# Hide cursor
DISPLAY=:0 unclutter -idle 0 -root -noevents >/dev/null 2>&1 &

# Warm up networking: wait for DNS, then HTTP; fall back to a local "offline" page
for i in {1..30}; do
  getent hosts "$(printf %s "$URL" | sed -n 's|^[a-z]*://\([^/:]\+\).*|\1|p')" >/dev/null 2>&1 && break
  sleep 1
done

OFFLINE_HTML="$HOME/kiosk-offline.html"
cat > "$OFFLINE_HTML" <<'HTML'
<!doctype html><meta charset="utf-8"><title>Service not reachable</title>
<style>html,body{height:100%;margin:0;display:grid;place-items:center;background:#0b132b;color:#e0e6f8;font:18px system-ui}
.box{max-width:700px;padding:24px;border-radius:16px;background:#1c2541}</style>
<div class="box">
  <h1>Trying to reach the server…</h1>
  <p>Network is up but the target URL isn’t responding yet. This page will be replaced automatically.</p>
</div>
HTML

# --- Watchdog: restart browser if HA is unresponsive for too long ---
WATCHDOG_FAILS=0

# Validate watchdog URL format to prevent command injection
if [[ ! "$PULSE_WATCHDOG_URL" =~ ^https?://[a-zA-Z0-9._-]+(:[0-9]+)?(/.*)?$ ]]; then
  echo "$(date): Invalid PULSE_WATCHDOG_URL format - watchdog disabled"
  PULSE_WATCHDOG_LIMIT=999999  # Effectively disable watchdog
fi

watchdog_loop() {
  while true; do
    if curl -sf --max-time 10 "$PULSE_WATCHDOG_URL" >/dev/null; then
      if (( WATCHDOG_FAILS >= 2 )); then
        echo "$(date): Watchdog recovered after $WATCHDOG_FAILS failures; restarting browser"
        pkill -f 'chromium.*--kiosk' || true
      fi
      WATCHDOG_FAILS=0
    else
      WATCHDOG_FAILS=$((WATCHDOG_FAILS + 1))
      echo "$(date) Watchdog: failure $WATCHDOG_FAILS"
      if (( WATCHDOG_FAILS >= PULSE_WATCHDOG_LIMIT )); then
        echo "$(date) Watchdog: restarting Chromium"
        pkill -f 'chromium.*--kiosk' || true
        WATCHDOG_FAILS=0
      fi
      if (( WATCHDOG_FAILS >= PULSE_WATCHDOG_LIMIT * 3 ));  then
        echo "$(date) Watchdog: hard reboot"
        if command -v "$SAFE_REBOOT" >/dev/null 2>&1; then
          sudo "$SAFE_REBOOT" "kiosk-watchdog: failures"
        else
          sudo reboot
        fi
      fi
    fi
    sleep "$PULSE_WATCHDOG_INTERVAL"
  done
}

watchdog_loop &

# Launch Chromium in a relaunch loop
BROWSER="$(command -v chromium || command -v chromium-browser)"
while true; do
  "$BROWSER" \
    --v=0 \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --disable-application-cache \
    --disk-cache-size=1 \
    --allow-running-insecure-content \
    --remote-allow-origins=http://localhost:9222 \
    --disable-extensions-except="$HOME/cursorless" \
    --load-extension="$HOME/cursorless" \
    --user-data-dir="$HOME/.config/kiosk-profile" \
    --disk-cache-dir=/tmp/kiosk-cache \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --noerrdialogs \
    --disable-infobars \
    --hide-scrollbars \
    --disable-gcm-service-worker \
    --disable-cloud-import \
    --disable-sync \
    --disable-logging \
    --disable-features=RendererCodeIntegrity,PreconnectToSearch,OptimizationHints,AutofillServerCommunication,PushMessaging \
    --disable-breakpad \
    --kiosk \
    --start-fullscreen \
    --window-position=0,0 \
    --force-device-scale-factor=1 \
    --high-dpi-support=1 \
    "$URL" \
    2>>"$HOME/kiosk-chrome-errors.log" \
  || true
  echo "$(date) chromium exited; restarting in 2s"
  sleep 2
done
