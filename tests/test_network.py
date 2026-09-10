from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pulse import network
from pulse.network import check_network, signal_bars, status_payload


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SignalBarsTests(unittest.TestCase):
    def test_no_reading_means_no_bars(self) -> None:
        # Not "zero bars" — an unanswerable probe must never render as a bad signal any
        # more than it renders as a good one.
        self.assertIsNone(signal_bars(None))

    def test_thresholds(self) -> None:
        for dbm, expected in ((-30, 4), (-55, 4), (-56, 3), (-65, 3), (-66, 2), (-75, 2), (-76, 1), (-95, 1)):
            with self.subTest(dbm=dbm):
                self.assertEqual(signal_bars(dbm), expected)


class SignalDbmTests(unittest.TestCase):
    def _read(self, body: str, interface: str = "wlan0") -> int | None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "wireless"
            _write(path, body)
            with mock.patch.object(network, "_PROC_WIRELESS", path):
                return network._signal_dbm(interface)

    _HEADER = (
        "Inter-| sta-|   Quality        |   Discarded packets\n face | tus | link level noise |  nwid  crypt   frag\n"
    )

    def test_parses_the_trailing_dot_form(self) -> None:
        # iwlib writes the level column as a float with a bare trailing dot.
        self.assertEqual(self._read(self._HEADER + " wlan0: 0000   62.  -48.  -256        0      0      0\n"), -48)

    def test_wraps_unsigned_levels_back_into_dbm(self) -> None:
        # Some drivers report the level as an unsigned 0-255 byte, where 208 is -48 dBm.
        # Taken at face value that reads as a wildly positive signal and pins the pill
        # at four bars on a link that is actually failing.
        self.assertEqual(self._read(self._HEADER + " wlan0: 0000   62.  208.  0.\n"), -48)

    def test_zero_level_is_unknown_not_perfect(self) -> None:
        # An interface that is up but unassociated reports a flat 0, which would
        # otherwise map straight to a triumphant four bars.
        self.assertIsNone(self._read(self._HEADER + " wlan0: 0000   0.  0.  0.\n"))

    def test_other_interfaces_are_not_confused_for_ours(self) -> None:
        body = self._HEADER + " wlan1: 0000   62.  -48.  -256\n"
        self.assertIsNone(self._read(body, interface="wlan0"))

    def test_missing_file_is_unknown(self) -> None:
        with mock.patch.object(network, "_PROC_WIRELESS", Path("/nonexistent/net/wireless")):
            self.assertIsNone(network._signal_dbm("wlan0"))

    def test_malformed_line_is_unknown(self) -> None:
        self.assertIsNone(self._read(self._HEADER + " wlan0: 0000\n"))


class CheckNetworkTests(unittest.TestCase):
    """End-to-end reads against a synthetic /sys/class/net."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.object(network, "_NET_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _wifi(self, name: str = "wlan0", *, operstate: str = "up") -> None:
        (self.root / name / "wireless").mkdir(parents=True)
        _write(self.root / name / "operstate", operstate)

    def _ethernet(self, name: str = "eth0", *, carrier: str = "0", operstate: str = "down") -> None:
        _write(self.root / name / "carrier", carrier)
        _write(self.root / name / "operstate", operstate)

    def _check(self, *, dbm: int | None = -48, ssid: str = "Graystorm", ip: str = "192.168.1.50"):
        with (
            mock.patch.object(network, "_signal_dbm", return_value=dbm),
            mock.patch.object(network, "_wireless_identity", return_value=(ssid, "AA:BB:CC:DD:EE:FF")),
            mock.patch.object(network, "_ip_address", return_value=ip),
        ):
            return check_network()

    def test_healthy_wifi_with_no_cable(self) -> None:
        # The normal state of every kiosk in this fleet.
        self._wifi()
        self._ethernet()
        status = self._check()
        self.assertTrue(status.wifi_up)
        self.assertEqual(status.wifi_bars, 4)
        self.assertEqual(status.wifi_ssid, "Graystorm")
        self.assertEqual(status.ethernet, "absent")

    def test_wifi_down_reports_no_bars_and_skips_the_radio_probes(self) -> None:
        # Nothing is asked of a down radio: the ioctls would just block against a driver
        # that has nothing to say, on a poll loop, forever.
        self._wifi(operstate="down")
        self._ethernet()
        with (
            mock.patch.object(network, "_signal_dbm") as dbm,
            mock.patch.object(network, "_wireless_identity") as identity,
        ):
            status = check_network()
        self.assertFalse(status.wifi_up)
        self.assertIsNone(status.wifi_bars)
        self.assertTrue(status.wifi_present)
        dbm.assert_not_called()
        identity.assert_not_called()

    def test_associated_but_no_signal_reading_is_not_four_bars(self) -> None:
        self._wifi()
        self._ethernet()
        status = self._check(dbm=None)
        self.assertTrue(status.wifi_up)
        self.assertIsNone(status.wifi_bars)

    def test_working_cable_is_up(self) -> None:
        self._wifi()
        self._ethernet(carrier="1", operstate="up")
        status = self._check()
        self.assertEqual(status.ethernet, "up")
        self.assertEqual(status.ethernet_ip, "192.168.1.50")

    def test_live_cable_with_no_address_is_down(self) -> None:
        # Carrier but no lease is the one Ethernet fault worth a red dot: something is
        # plugged in, the link negotiated, and it still isn't working.
        self._wifi()
        self._ethernet(carrier="1", operstate="up")
        status = self._check(ip="")
        self.assertEqual(status.ethernet, "down")

    def test_no_ethernet_interface_at_all(self) -> None:
        self._wifi()
        status = self._check()
        self.assertEqual(status.ethernet, "none")
        self.assertEqual(status.ethernet_interface, "")

    def test_virtual_interfaces_are_never_picked(self) -> None:
        # docker0 has a carrier and would otherwise be reported as this kiosk's working
        # Ethernet link, painting a green dot on a display with nothing plugged into it.
        _write(self.root / "lo" / "operstate", "unknown")
        _write(self.root / "docker0" / "carrier", "1")
        _write(self.root / "docker0" / "operstate", "up")
        _write(self.root / "veth123" / "carrier", "1")
        self._wifi()
        self._ethernet()
        status = self._check()
        self.assertEqual(status.ethernet_interface, "eth0")
        self.assertEqual(status.ethernet, "absent")

    def test_missing_sysfs_is_survivable(self) -> None:
        # A container or a stripped-down image has no /sys/class/net to read. That is a
        # "no opinion" pill, not a crashed poll thread.
        with mock.patch.object(network, "_NET_ROOT", self.root / "nope"):
            status = check_network()
        self.assertFalse(status.wifi_present)
        self.assertEqual(status.ethernet, "none")

    def test_payload_round_trips_every_rendered_field(self) -> None:
        self._wifi()
        self._ethernet()
        payload = status_payload(self._check())
        for key in ("wifi_present", "wifi_up", "bars", "dbm", "ssid", "bssid", "ethernet"):
            self.assertIn(key, payload)
        self.assertEqual(payload["bars"], 4)
        self.assertEqual(payload["ethernet"], "absent")


if __name__ == "__main__":
    unittest.main()
