#!/usr/bin/env bash
# Boot-time safety net for BSSID-pinned kiosks (see configure_wifi / the
# wifi-band-policy file).
#
# A device pinned to a single BSSID has no fallback if that AP is down, moved
# to a DFS channel it can't join, or replaced — it would boot stranded,
# offline. This one-shot catches that: if a pinned device can't reach any
# upstream within the timeout, it reverts to unpinned 2.4 GHz (band=bg, BSSID
# cleared) and reboots, so the kiosk comes back online (streaming will buffer,
# but online beats stranded).
#
# Self-limiting: it only acts when a pin is present (band=a, or band=bg with a
# BSSID lock), so once it has reverted to unpinned bg it does nothing on
# subsequent boots — no reboot loop, even during a real network outage.
# configure_wifi re-applies the policy pin on the next app upgrade (setup.sh
# run), so recovery is automatic once the AP/BSSID is back.
#
# Health is judged by pulse-net-check.sh (a real TCP round-trip), NOT a gateway
# ping — the wedged brcmfmac firmware answers gateway pings while the datapath
# is dead, which would mask a genuine failure.
set -uo pipefail

# First Wi-Fi connection profile (these devices have exactly one).
con=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null \
    | awk -F: '$2=="802-11-wireless" || $2=="wifi" {print $1; exit}' || true)
[ -n "$con" ] || exit 0

band=$(nmcli -g 802-11-wireless.band connection show "$con" 2>/dev/null || echo "")
bssid=$(nmcli -g 802-11-wireless.bssid connection show "$con" 2>/dev/null || echo "")
# Only guard pinned devices: 5 GHz (band=a), or 2.4 GHz locked to one BSSID.
# Unpinned bg is already the fallback state — nothing to revert to.
if [ "$band" != "a" ] && [ -z "$bssid" ]; then
    exit 0
fi

# Give the pinned association up to ~150s to come up and reach upstream.
for _ in $(seq 1 30); do
    if /usr/local/sbin/pulse-net-check.sh; then
        exit 0    # healthy on the pin — nothing to do
    fi
    sleep 5
done

logger -t pulse-wifi-band-fallback \
    "Pin (band=$band${bssid:+, bssid $bssid}) on '$con' unreachable after boot; reverting to unpinned bg and rebooting"
nmcli connection modify "$con" 802-11-wireless.band bg 802-11-wireless.bssid "" \
    || logger -t pulse-wifi-band-fallback "Warning: nmcli revert reported failure."

# Only reboot if the revert actually took. If the pin is still in place (nmcli
# failed, D-Bus wedged, etc.), rebooting would re-enter this exact path on the
# next boot — pinned, unreachable, revert fails, reboot — an endless reboot
# loop. In that case leave the device up (stranded but stable) and let the next
# setup.sh run re-attempt the revert.
new_band=$(nmcli -g 802-11-wireless.band connection show "$con" 2>/dev/null || echo "")
new_bssid=$(nmcli -g 802-11-wireless.bssid connection show "$con" 2>/dev/null || echo "")
if [ "$new_band" = "bg" ] && [ -z "$new_bssid" ]; then
    systemctl reboot
else
    logger -t pulse-wifi-band-fallback \
        "Still pinned (band=$new_band${new_bssid:+, bssid $new_bssid}) after revert attempt; NOT rebooting to avoid a reboot loop — setup.sh will retry."
    exit 1
fi
