"""Tests for speaker reachability detection."""

from __future__ import annotations

import unittest
from unittest import mock

from pulse.speaker import (
    SpeakerConfig,
    bt_connected,
    check_speaker,
    resolve_bt_mac,
    wired_sink_present,
)

# Trimmed to the lines the parser cares about.
_INFO_CONNECTED = """Device AA:BB:CC:DD:EE:FF (public)
\tName: Living Room
\tAlias: Living Room
\tPaired: yes
\tConnected: yes
"""

_INFO_DISCONNECTED = _INFO_CONNECTED.replace("Connected: yes", "Connected: no")

_DEVICES_PAIRED = "Device AA:BB:CC:DD:EE:FF Living Room\nDevice 11:22:33:44:55:66 Other Speaker\n"

_SINKS_SHORT = (
    "72\talsa_output.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_-00.analog-stereo\tPipeWire\ts24le 2ch\tSUSPENDED\n"
    "74\talsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo\tPipeWire\ts16le 2ch\tSUSPENDED\n"
)


def _patch_run(side_effect):
    return mock.patch("pulse.speaker._run", side_effect=side_effect)


class BtConnectedTests(unittest.TestCase):
    def test_reports_connected(self) -> None:
        with _patch_run(lambda args: _INFO_CONNECTED):
            self.assertIs(bt_connected("AA:BB:CC:DD:EE:FF"), True)

    def test_reports_disconnected(self) -> None:
        with _patch_run(lambda args: _INFO_DISCONNECTED):
            self.assertIs(bt_connected("AA:BB:CC:DD:EE:FF"), False)

    def test_unknown_when_bluetoothctl_fails(self) -> None:
        # Distinct from False: a wedged or missing BlueZ must not be reported as a
        # speaker someone needs to go power-cycle.
        with _patch_run(lambda args: None):
            self.assertIsNone(bt_connected("AA:BB:CC:DD:EE:FF"))

    def test_unknown_when_device_not_in_bluez(self) -> None:
        with _patch_run(lambda args: "Device AA:BB:CC:DD:EE:FF not available\n"):
            self.assertIsNone(bt_connected("AA:BB:CC:DD:EE:FF"))


class ResolveBtMacTests(unittest.TestCase):
    def test_configured_mac_wins_and_picks_up_its_name(self) -> None:
        with _patch_run(lambda args: _DEVICES_PAIRED):
            self.assertEqual(resolve_bt_mac("aa:bb:cc:dd:ee:ff"), ("AA:BB:CC:DD:EE:FF", "Living Room"))

    def test_configured_mac_kept_even_when_not_paired(self) -> None:
        # The speaker being absent from BlueZ is exactly the condition we're detecting,
        # so an unmatched MAC must still be watched — just without a friendly name.
        with _patch_run(lambda args: ""):
            self.assertEqual(resolve_bt_mac("AA:BB:CC:DD:EE:FF"), ("AA:BB:CC:DD:EE:FF", ""))

    def test_malformed_mac_is_ignored(self) -> None:
        with _patch_run(lambda args: _DEVICES_PAIRED):
            self.assertEqual(resolve_bt_mac("not-a-mac"), ("", ""))

    def test_falls_back_to_first_paired_device(self) -> None:
        with _patch_run(lambda args: "" if args[2] == "Connected" else _DEVICES_PAIRED):
            self.assertEqual(resolve_bt_mac(""), ("AA:BB:CC:DD:EE:FF", "Living Room"))

    def test_nothing_to_watch_when_no_devices(self) -> None:
        with _patch_run(lambda args: ""):
            self.assertEqual(resolve_bt_mac(""), ("", ""))


class WiredSinkTests(unittest.TestCase):
    def test_finds_sink_by_substring(self) -> None:
        with _patch_run(lambda args: _SINKS_SHORT):
            self.assertIs(wired_sink_present("C-Media"), True)

    def test_missing_sink(self) -> None:
        with _patch_run(lambda args: _SINKS_SHORT):
            self.assertIs(wired_sink_present("Unitek"), False)

    def test_ignores_monitor_sinks(self) -> None:
        sinks = "1\talsa_output.usb-C-Media_Foo.analog-stereo.monitor\tPipeWire\ts16le\tIDLE\n"
        with _patch_run(lambda args: sinks):
            self.assertIs(wired_sink_present("C-Media"), False)

    def test_unknown_when_pactl_fails(self) -> None:
        with _patch_run(lambda args: None):
            self.assertIsNone(wired_sink_present("C-Media"))


class CheckSpeakerTests(unittest.TestCase):
    def test_bluetooth_offline(self) -> None:
        def run(args: list[str]) -> str:
            return _DEVICES_PAIRED if args[1] == "devices" else _INFO_DISCONNECTED

        with _patch_run(run):
            status = check_speaker(SpeakerConfig(bt_autoconnect=True, bt_mac="AA:BB:CC:DD:EE:FF"))
        assert status is not None
        self.assertTrue(status.offline)
        self.assertEqual(status.name, "Living Room")
        self.assertEqual(status.kind, "bluetooth")

    def test_bluetooth_online(self) -> None:
        def run(args: list[str]) -> str:
            return _DEVICES_PAIRED if args[1] == "devices" else _INFO_CONNECTED

        with _patch_run(run):
            status = check_speaker(SpeakerConfig(bt_autoconnect=True, bt_mac="AA:BB:CC:DD:EE:FF"))
        assert status is not None
        self.assertFalse(status.offline)

    def test_no_opinion_when_nothing_configured(self) -> None:
        # A display with autoconnect off and no wired sink named has no expectation to
        # violate, so it must never show the badge.
        with _patch_run(lambda args: _SINKS_SHORT):
            self.assertIsNone(check_speaker(SpeakerConfig(bt_autoconnect=False)))

    def test_stale_pairing_alone_does_not_trigger(self) -> None:
        # pulse-office keeps a pairing to a speaker that now lives in the kitchen. With
        # autoconnect off that pairing must be invisible here, or the badge would never
        # clear on a re-purposed display.
        with _patch_run(lambda args: _DEVICES_PAIRED):
            self.assertIsNone(check_speaker(SpeakerConfig(bt_autoconnect=False, bt_mac="AA:BB:CC:DD:EE:FF")))

    def test_wired_speaker_unplugged(self) -> None:
        config = SpeakerConfig(bt_autoconnect=False, wired_sink="Unitek")
        with _patch_run(lambda args: _SINKS_SHORT):
            status = check_speaker(config)
        assert status is not None
        self.assertTrue(status.offline)
        self.assertEqual(status.kind, "wired")

    def test_wired_speaker_present(self) -> None:
        config = SpeakerConfig(bt_autoconnect=False, wired_sink="C-Media")
        with _patch_run(lambda args: _SINKS_SHORT):
            status = check_speaker(config)
        assert status is not None
        self.assertFalse(status.offline)

    def test_falls_through_to_wired_when_no_bluetooth_device_exists(self) -> None:
        # Autoconnect defaults to true, so a wired-speaker display that never turned it
        # off would otherwise get stuck on the Bluetooth branch and skip its own check.
        def run(args: list[str]) -> str:
            return "" if args[0] == "bluetoothctl" else _SINKS_SHORT

        with _patch_run(run):
            status = check_speaker(SpeakerConfig(bt_autoconnect=True, wired_sink="Unitek"))
        assert status is not None
        self.assertTrue(status.offline)
        self.assertEqual(status.kind, "wired")

    def test_probe_failure_is_not_an_outage(self) -> None:
        with _patch_run(lambda args: None):
            self.assertIsNone(check_speaker(SpeakerConfig(bt_autoconnect=True, bt_mac="AA:BB:CC:DD:EE:FF")))


if __name__ == "__main__":
    unittest.main()
