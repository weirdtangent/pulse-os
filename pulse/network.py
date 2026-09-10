"""Read this kiosk's WiFi signal strength and Ethernet link state.

A Pulse display hangs on a wall with no keyboard, so "is it on the network, and how
well?" is a question that today can only be answered over SSH -- which is exactly the
thing that stops working when the answer is bad. The whole point of putting this on
the bar is that it stays readable while the network is degrading: a kiosk drifting
from four bars to one is the visible, *early* form of the roam-stall and power-save
failures this fleet has hit repeatedly.

Everything here reads sysfs and procfs directly rather than shelling out to `iw` or
`nmcli`. Those are a fork per poll on a Pi Zero-class CPU, they are not installed
uniformly across the fleet, and both block for seconds against a wedged driver --
whereas the kernel keeps these files answerable even when the radio is unhappy. The
one exception is the SSID/BSSID pair, which has no sysfs equivalent; that goes
through an ioctl on a plain socket, still with no subprocess.

The module deliberately reports "unknown" rather than guessing. A probe that cannot
run must never render as a healthy green pill -- a display that has quietly stopped
knowing its own link state is itself the thing worth showing.
"""

from __future__ import annotations

import array
import fcntl
import logging
import socket
import struct
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_NET_ROOT = Path("/sys/class/net")
_PROC_WIRELESS = Path("/proc/net/wireless")

# Virtual and container interfaces are not this kiosk's link to the house. Matching on
# prefixes keeps the fleet's mix of predictable (enxAABB) and legacy (eth0) names
# working without an allow-list that has to be edited per device.
_IGNORED_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap", "wg", "zt", "sit", "dummy")

# SIOCGIWESSID / SIOCGIWAP from linux/wireless.h. Present on every kernel that has
# CONFIG_WIRELESS_EXT, which includes the brcmfmac Pis this fleet runs.
_SIOCGIWESSID = 0x8B1B
_SIOCGIWAP = 0x8B15
_IW_ESSID_MAX_SIZE = 32

# Signal thresholds in dBm, strongest first. These are the practical breakpoints for
# 2.4/5GHz on the Pi radios here, not the vendor marketing curve: -55 and better is a
# link with headroom to spare, -75 and worse is where throughput collapses and the
# roam-stall symptoms start. One bar therefore means "this is already a problem",
# which is the reading that makes the pill worth glancing at.
_BAR_THRESHOLDS_DBM = ((-55, 4), (-65, 3), (-75, 2))


@dataclass(frozen=True)
class NetworkStatus:
    """One reading of this kiosk's connectivity.

    ``wifi_bars`` is 1-4, or None when there is no bar count to draw — the radio is
    down or unassociated, there is no WiFi interface, or the driver gave no usable
    signal level. There is deliberately no zero: an associated link always gets at
    least one bar, and "nothing to show" has to stay distinguishable from "shown as
    the worst possible signal", because only one of those is a measurement.
    """

    wifi_present: bool
    wifi_up: bool
    wifi_bars: int | None
    wifi_dbm: int | None
    wifi_ssid: str
    wifi_bssid: str
    wifi_ip: str
    wifi_interface: str
    # "up" (carrier and usable), "down" (cable in, no link), "absent" (no cable), or
    # "none" (no Ethernet interface on this device at all).
    ethernet: str
    ethernet_ip: str
    ethernet_interface: str


def _read(path: Path) -> str:
    """First line of a sysfs file, or "" when it cannot be read.

    Never raises: every caller is on a background poll loop, and sysfs entries vanish
    mid-read when an interface is torn down.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, ValueError) as exc:
        _LOGGER.debug("[network] could not read %s: %s", path, exc)
        return ""


def _interfaces() -> list[str]:
    try:
        names = sorted(entry.name for entry in _NET_ROOT.iterdir())
    except OSError as exc:
        _LOGGER.debug("[network] could not list %s: %s", _NET_ROOT, exc)
        return []
    return [name for name in names if not name.startswith(_IGNORED_PREFIXES)]


def _is_wireless(name: str) -> bool:
    return (_NET_ROOT / name / "wireless").is_dir()


def _ip_address(name: str) -> str:
    """IPv4 address bound to ``name``, or "" if it has none.

    SIOCGIFADDR rather than parsing `ip addr`, for the same no-subprocess reason as the
    rest of the module. An interface that is up but has no address answers with EADDRNOTAVAIL,
    which is a meaningful state here -- associated to the AP but DHCP never completed.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = fcntl.ioctl(
                sock.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", name[:15].encode("utf-8")),
            )
        return socket.inet_ntoa(packed[20:24])
    except OSError as exc:
        _LOGGER.debug("[network] no IPv4 on %s: %s", name, exc)
        return ""


def _wireless_identity(name: str) -> tuple[str, str]:
    """(ssid, bssid) for an associated interface, ("", "") when unassociated.

    The BSSID matters as much as the SSID on this fleet: a kiosk pinned to an AP that
    has rebooted keeps the SSID and silently loses the pin, so "which AP am I actually
    on" is the diagnostic somebody standing in front of the display needs.
    """
    ssid = ""
    bssid = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            iface = name[:15].encode("utf-8")
            buffer = array.array("B", b"\x00" * (_IW_ESSID_MAX_SIZE + 1))
            addr, length = buffer.buffer_info()
            request = struct.pack("16sPHH", iface, addr, length, 0)
            try:
                fcntl.ioctl(sock.fileno(), _SIOCGIWESSID, request)
                ssid = buffer.tobytes().split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            except OSError as exc:
                _LOGGER.debug("[network] no ESSID on %s: %s", name, exc)
            try:
                packed = fcntl.ioctl(sock.fileno(), _SIOCGIWAP, struct.pack("256s", iface))
                raw = packed[18:24]
                # An unassociated radio answers with the all-zero AP address rather than
                # an error, so that has to be filtered out here or it renders as a real
                # (and very confusing) 00:00:00:00:00:00 "access point".
                if any(raw):
                    bssid = ":".join(f"{octet:02X}" for octet in raw)
            except OSError as exc:
                _LOGGER.debug("[network] no AP address on %s: %s", name, exc)
    except OSError as exc:
        _LOGGER.debug("[network] wireless identity failed for %s: %s", name, exc)
    return ssid, bssid


def _signal_dbm(name: str) -> int | None:
    """Signal level in dBm from /proc/net/wireless, or None when unavailable.

    The level column is written as a float with a trailing dot ("-42.") and, on drivers
    that report it unsigned, as a 0-255 value that has to be wrapped back into negative
    dBm. Both forms show up across this fleet's radios.
    """
    try:
        lines = _PROC_WIRELESS.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        _LOGGER.debug("[network] could not read %s: %s", _PROC_WIRELESS, exc)
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(f"{name}:"):
            continue
        fields = stripped.split(":", 1)[1].split()
        if len(fields) < 3:
            return None
        try:
            level = int(float(fields[2].rstrip(".")))
        except ValueError:
            return None
        if level > 0:
            level -= 256
        # A driver that has the interface up but no association reports a flat 0, which
        # would otherwise map to a triumphant four bars.
        return level if level < 0 else None
    return None


def signal_bars(dbm: int | None) -> int | None:
    """Map a dBm reading onto the 0-4 bars the pill draws."""
    if dbm is None:
        return None
    for threshold, bars in _BAR_THRESHOLDS_DBM:
        if dbm >= threshold:
            return bars
    return 1


def check_network() -> NetworkStatus:
    """One reading of WiFi and Ethernet state for this kiosk.

    Picks the first non-virtual interface of each kind. Every Pulse display has exactly
    one of each, so there is nothing to disambiguate; a device with two would simply
    report the lower-named one rather than inventing a policy nobody needs.
    """
    wifi_name = ""
    ethernet_name = ""
    for name in _interfaces():
        if _is_wireless(name):
            if not wifi_name:
                wifi_name = name
        elif not ethernet_name:
            ethernet_name = name

    wifi_up = False
    wifi_dbm: int | None = None
    wifi_ssid = ""
    wifi_bssid = ""
    wifi_ip = ""
    if wifi_name:
        wifi_up = _read(_NET_ROOT / wifi_name / "operstate") == "up"
        if wifi_up:
            wifi_ssid, wifi_bssid = _wireless_identity(wifi_name)
            wifi_dbm = _signal_dbm(wifi_name)
            wifi_ip = _ip_address(wifi_name)

    # "absent" and "down" both mean no traffic is flowing, but only one of them is worth
    # a red dot. Every Pi in this fleet has an eth0 with nothing plugged into it, so
    # treating a missing carrier as a fault would paint a permanent red dot on all four
    # displays and teach everyone to ignore the pill -- which costs us the one case that
    # matters, a cable that *is* plugged in and has stopped working.
    ethernet = "none"
    ethernet_ip = ""
    if ethernet_name:
        carrier = _read(_NET_ROOT / ethernet_name / "carrier")
        operstate = _read(_NET_ROOT / ethernet_name / "operstate")
        if carrier == "1":
            ethernet_ip = _ip_address(ethernet_name)
            # Carrier without an address is a live cable that never finished DHCP --
            # a real fault, and exactly the one a red dot should be spent on.
            ethernet = "up" if ethernet_ip else "down"
        elif operstate == "down" or carrier == "0":
            ethernet = "absent"
        else:
            # No carrier file at all, or an unreadable one: no opinion.
            ethernet = "absent"

    return NetworkStatus(
        wifi_present=bool(wifi_name),
        wifi_up=wifi_up,
        wifi_bars=signal_bars(wifi_dbm) if wifi_up else None,
        wifi_dbm=wifi_dbm,
        wifi_ssid=wifi_ssid,
        wifi_bssid=wifi_bssid,
        wifi_ip=wifi_ip,
        wifi_interface=wifi_name,
        ethernet=ethernet,
        ethernet_ip=ethernet_ip,
        ethernet_interface=ethernet_name,
    )


def status_payload(status: NetworkStatus) -> dict[str, object]:
    """Flatten a reading into the dict the overlay snapshot carries."""
    return {
        "wifi_present": status.wifi_present,
        "wifi_up": status.wifi_up,
        "bars": status.wifi_bars,
        "dbm": status.wifi_dbm,
        "ssid": status.wifi_ssid,
        "bssid": status.wifi_bssid,
        "wifi_ip": status.wifi_ip,
        "wifi_interface": status.wifi_interface,
        "ethernet": status.ethernet,
        "ethernet_ip": status.ethernet_ip,
        "ethernet_interface": status.ethernet_interface,
    }
