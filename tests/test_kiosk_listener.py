"""Tests for helpers in bin/kiosk-mqtt-listener.py.

The listener is a script rather than a package module, so it is loaded by path. Only pure
helpers are exercised here — nothing that touches MQTT, audio, or the display.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_LISTENER = Path(__file__).resolve().parent.parent / "bin" / "kiosk-mqtt-listener.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kiosk_mqtt_listener", _LISTENER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["kiosk_mqtt_listener"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def listener() -> ModuleType:
    return _load()


# -- font stack fallbacks -----------------------------------------------------


@pytest.mark.parametrize(
    "configured",
    [
        "Inter",  # the shipped default, not installed on a Pi
        "DejaVu Serif",  # contains "serif" but is a real family, not a generic
        "Liberation Serif",
        "Noto Sans Mono",  # contains neither "monospace" nor a generic
    ],
)
def test_stack_without_a_generic_gets_one_appended(listener, configured):
    """A stack ending in a real family has nowhere to go when that family is missing.

    Substring matching got this wrong: "DejaVu Serif" contains "serif", so it looked like
    it already had a generic and was left without a fallback.
    """
    result = listener._with_generic_fallback(configured)
    assert result.startswith(configured)
    assert result.endswith('sans-serif, "Noto Color Emoji"')


@pytest.mark.parametrize(
    "configured",
    ['"Inter", sans-serif', "monospace", "Georgia, serif", '"Menlo", monospace'],
)
def test_stack_that_already_ends_in_a_generic_is_untouched(listener, configured):
    assert listener._with_generic_fallback(configured) == configured


def test_empty_stack_falls_back_to_the_shipped_default(listener):
    assert listener._with_generic_fallback("") == listener.DEFAULT_FONT_STACK


# -- font list filtering ------------------------------------------------------


@pytest.mark.parametrize("name", ["D050000L", "Standard Symbols PS", "Noto Color Emoji", "Z003"])
def test_symbol_families_are_not_offered(listener, name):
    """Choosing one renders the overlay as gibberish."""
    assert listener._is_non_text_font(name)


@pytest.mark.parametrize("name", ["DejaVu Sans", "Liberation Sans", "Nimbus Roman", "URW Gothic"])
def test_text_families_are_offered(listener, name):
    assert not listener._is_non_text_font(name)


# -- Home Assistant recovery state machine ------------------------------------
#
# _check_ha_recovery decides whether an HA outage should trigger a dashboard
# reload. It is driven here against a stub rather than a real listener so no
# MQTT, DevTools or HTTP is involved -- only the state machine is under test.


class _Recorder:
    """Minimal stand-in exposing just what _check_ha_recovery touches."""

    def __init__(self, listener, reachable: bool = True):
        self.config = SimpleNamespace(ha_base_url="http://ha.example", ha_token="tok")
        self.reachable = reachable
        self.homes = 0
        self.logs: list[str] = []
        self._ha_unreachable_since = None
        self._ha_pending_home_since = None
        self._ha_pending_outage = 0.0
        self._last_ha_recovery_home = None
        self._check_ha_recovery = listener.KioskMqttListener._check_ha_recovery.__get__(self)

    def _probe_home_assistant(self) -> bool:
        return self.reachable

    def handle_home(self) -> None:
        self.homes += 1

    def log(self, message: str) -> None:
        self.logs.append(message)


def _outage(listener, *, seconds: float, settle: float):
    """Run one full down->up cycle and return the stub after settling."""
    r = _Recorder(listener)
    r.reachable = False
    r._check_ha_recovery(0.0)  # outage starts
    r._check_ha_recovery(seconds)  # still down
    r.reachable = True
    r._check_ha_recovery(seconds)  # first success -> starts settle timer
    r._check_ha_recovery(seconds + settle)
    return r


def test_restart_length_outage_sends_the_kiosk_home(listener):
    r = _outage(listener, seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert r.homes == 1


def test_brief_blip_does_not_reload(listener):
    # Shorter than HA_RECOVERY_MIN_OUTAGE_SECONDS: a single failed probe or a
    # momentary network wobble must never reload a working dashboard.
    r = _outage(
        listener,
        seconds=listener.HA_RECOVERY_MIN_OUTAGE_SECONDS - 1,
        settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1,
    )
    assert r.homes == 0


def test_reload_waits_for_the_settle_window(listener):
    # /api/ answers before the frontend can serve the dashboard, so reloading on
    # the first successful probe would just re-strand the kiosk.
    r = _outage(listener, seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS - 1)
    assert r.homes == 0


def test_outage_during_settle_cancels_the_pending_reload(listener):
    r = _Recorder(listener)
    r.reachable = False
    r._check_ha_recovery(0.0)
    r.reachable = True
    r._check_ha_recovery(300.0)  # pending reload armed
    r.reachable = False
    r._check_ha_recovery(310.0)  # HA drops again -> cancel
    r.reachable = True
    r._check_ha_recovery(320.0)  # too brief to re-arm
    r._check_ha_recovery(999.0)
    assert r.homes == 0


def test_flapping_ha_cannot_cause_a_reload_loop(listener):
    r = _outage(listener, seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert r.homes == 1
    base = 300 + listener.HA_RECOVERY_SETTLE_SECONDS + 1
    # A second full outage inside the rate limit must not reload again.
    r.reachable = False
    r._check_ha_recovery(base + 1)
    r._check_ha_recovery(base + 300)
    r.reachable = True
    r._check_ha_recovery(base + 300)
    r._check_ha_recovery(base + 300 + listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert r.homes == 1


def test_no_ha_credentials_means_no_probing(listener):
    r = _Recorder(listener)
    r.config = SimpleNamespace(ha_base_url="", ha_token="")
    r.reachable = False
    r._check_ha_recovery(0.0)
    assert r._ha_unreachable_since is None
    assert r.homes == 0
