#!/usr/bin/env bash
# Health check for the watchdog(8) daemon (test-binary).
#
# Exits non-zero when the default gateway is unreachable, which is the
# signature of the "soft hang" failure mode we have observed on Pulse devices:
# systemd stays alive (so it keeps petting the kernel watchdog) but networking
# is wedged and the device is effectively dead until a manual power-cycle.
#
# When this script keeps failing for longer than watchdog.conf's retry-timeout,
# the watchdog daemon stops petting /dev/watchdog and the BCM2835 hardware
# timer resets the board — recovery in minutes instead of hours, with no
# reliance on a clean reboot path (which may itself be wedged).
#
# Kept deliberately lean: the daemon runs this every `interval` seconds.
set -uo pipefail

# Default gateway, derived at runtime so this works on any network.
gw=$(ip route show default 2>/dev/null | awk '/default/{print $3; exit}')

# No default route at all is itself an unhealthy state.
[ -z "$gw" ] && exit 1

# A single quick ping; -W is the per-packet timeout in seconds.
ping -c1 -W3 "$gw" >/dev/null 2>&1 || exit 1

exit 0
