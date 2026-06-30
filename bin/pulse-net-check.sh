#!/usr/bin/env bash
# Health check for the watchdog(8) daemon (test-binary).
#
# Returns 0 (healthy) only when the device can complete a real TCP round-trip to
# at least one upstream host. A bare ICMP ping to the default gateway is NOT
# sufficient: the Pi's brcmfmac SDIO firmware keeps answering gateway pings even
# when the actual datapath is wedged. Observed 2026-06-30 on pulse-kitchen — the
# device went dark to all real traffic for ~9 min (no logs shipped, app dead)
# while the gateway stayed "reachable", so the old gateway-ping check never
# tripped and the board never self-reset. A TCP connect exercises the full TX/RX
# path through the firmware, so a wedge fails it.
#
# Targets default to the system's configured DNS servers (always-on infra the
# device already depends on, reachable only over the real datapath). The
# loopback stub (127.0.0.0/8, e.g. systemd-resolved) is excluded so it can never
# return a false "healthy" with the network down. Override or extend by setting
#   TARGETS=( "host:port" ... )
# in /etc/default/pulse-net-check.
#
# We report unhealthy only when ALL targets fail, so a single host going down
# for maintenance does not cause a needless reboot. When this keeps failing past
# watchdog.conf's retry-timeout, the daemon stops petting /dev/watchdog and the
# BCM2835 hardware timer resets the board — recovery in minutes, not hours.
#
# Kept deliberately lean: the daemon runs this every `interval` seconds.
set -uo pipefail

# No default route at all is itself an unhealthy state.
ip route show default 2>/dev/null | grep -q . || exit 1

# Default targets: the configured (non-loopback) DNS servers, on TCP/53.
declare -a TARGETS=()
while read -r ip; do
    [ -n "$ip" ] && TARGETS+=( "${ip}:53" )
done < <(
    {
        resolvectl dns 2>/dev/null
        awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null
    } | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | grep -vE '^127\.' | sort -u
)

# Allow site overrides / extra targets (may replace or append to TARGETS).
# shellcheck disable=SC1091
[ -r /etc/default/pulse-net-check ] && . /etc/default/pulse-net-check

# Last resort: if no usable target was found, fall back to the gateway on TCP/53
# (still a real round-trip, unlike an ICMP echo).
if [ "${#TARGETS[@]}" -eq 0 ]; then
    gw=$(ip route show default 2>/dev/null | awk '/default/{print $3; exit}')
    [ -n "$gw" ] && TARGETS+=( "${gw}:53" )
fi
[ "${#TARGETS[@]}" -eq 0 ] && exit 1

# Healthy as soon as any single target accepts a TCP connection.
for hp in "${TARGETS[@]}"; do
    h=${hp%:*}; p=${hp##*:}
    if timeout 3 bash -c "exec 3<>/dev/tcp/${h}/${p}" 2>/dev/null; then
        exec 3>&- 2>/dev/null || true
        exit 0
    fi
done

exit 1
