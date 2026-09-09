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
# _check_ha_recovery decides whether an HA outage should trigger a reload of the
# kiosk page. It is driven here against a stub rather than a real listener so no
# MQTT, DevTools or HTTP is involved -- only the state machine is under test.

_PULSE_URL = "http://ha.example/dashboard-pulse/home"


class _Recorder:
    """Minimal stand-in exposing just what _check_ha_recovery touches."""

    def __init__(self, listener, *, token: str = "tok", pulse_url: str = _PULSE_URL):
        self.config = SimpleNamespace(ha_base_url="http://ha.example", ha_token=token, pulse_url=pulse_url)
        self.reachable = True
        self.reloads: list[str] = []
        self.logs: list[str] = []
        self._last_navigated_url = None
        self._ha_unreachable_since = None
        self._ha_pending_home_since = None
        self._ha_pending_outage = 0.0
        self._last_ha_recovery_home = None
        cls = listener.KioskMqttListener
        self._check_ha_recovery = cls._check_ha_recovery.__get__(self)
        self._recovery_probe_target = cls._recovery_probe_target.__get__(self)

    def _probe_home_assistant(self) -> bool:
        return self.reachable

    def reload_url(self, url: str) -> bool:
        self.reloads.append(url)
        return True

    def log(self, message: str) -> None:
        self.logs.append(message)


def _outage(r, *, seconds: float, settle: float):
    """Run one full down->up cycle on an existing recorder."""
    r.reachable = False
    r._check_ha_recovery(0.0)
    r._check_ha_recovery(seconds)
    r.reachable = True
    r._check_ha_recovery(seconds)  # first success starts the settle timer
    r._check_ha_recovery(seconds + settle)
    return r


def test_restart_length_outage_reloads_the_kiosk(listener):
    r = _outage(_Recorder(listener), seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert r.reloads == [_PULSE_URL]


def test_brief_blip_does_not_reload(listener):
    # Shorter than HA_RECOVERY_MIN_OUTAGE_SECONDS: a single failed probe or a
    # momentary network wobble must never reload a working dashboard.
    r = _outage(
        _Recorder(listener),
        seconds=listener.HA_RECOVERY_MIN_OUTAGE_SECONDS - 1,
        settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1,
    )
    assert r.reloads == []


def test_reload_waits_for_the_settle_window(listener):
    # /api/ answers before the frontend can serve the dashboard, so reloading on
    # the first successful probe would just re-strand the kiosk.
    r = _outage(_Recorder(listener), seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS - 1)
    assert r.reloads == []


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
    assert r.reloads == []


def test_flapping_ha_cannot_cause_a_reload_loop(listener):
    r = _outage(_Recorder(listener), seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert len(r.reloads) == 1
    base = 300 + listener.HA_RECOVERY_SETTLE_SECONDS + 1
    r.reachable = False
    r._check_ha_recovery(base + 1)
    r._check_ha_recovery(base + 300)
    r.reachable = True
    r._check_ha_recovery(base + 300)
    r._check_ha_recovery(base + 300 + listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert len(r.reloads) == 1


# -- what gets reloaded -------------------------------------------------------


def test_recovery_restores_the_last_navigated_url(listener):
    # A kiosk driven elsewhere by kiosk/url/set (a camera view, a status page)
    # must come back to THAT page, not be snapped home by an unrelated outage.
    r = _Recorder(listener)
    r._last_navigated_url = "http://cam.example/stream"
    _outage(r, seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert r.reloads == ["http://cam.example/stream"]


def test_recovery_falls_back_to_pulse_url_when_nothing_was_navigated(listener):
    r = _outage(_Recorder(listener), seconds=300, settle=listener.HA_RECOVERY_SETTLE_SECONDS + 1)
    assert r.reloads == [_PULSE_URL]


# -- probe target selection ---------------------------------------------------


def test_probe_prefers_the_authenticated_api_when_a_token_exists(listener):
    url, headers = _Recorder(listener, token="tok")._recovery_probe_target()
    assert url == "http://ha.example/api/"
    assert headers["Authorization"] == "Bearer tok"


def test_probe_falls_back_to_pulse_url_without_a_token(listener):
    # HOME_ASSISTANT_TOKEN ships empty in pulse.conf.sample; requiring it would
    # silently disable recovery for every install that never minted one.
    url, headers = _Recorder(listener, token="")._recovery_probe_target()
    assert url == _PULSE_URL
    assert headers == {}


def test_no_token_and_no_pulse_url_disables_recovery(listener):
    r = _Recorder(listener, token="", pulse_url="")
    r.config.ha_base_url = ""
    assert r._recovery_probe_target() is None
    r.reachable = False
    r._check_ha_recovery(0.0)
    assert r._ha_unreachable_since is None
    assert r.reloads == []


# -- update button re-check ---------------------------------------------------
#
# _ensure_update_availability exists so an explicit "update now" is never refused
# because the cached availability flag is stale. Driven against a stub: no MQTT,
# no HTTP, no threads.


class _UpdateStub:
    def __init__(self, listener, *, cached: bool, after_refresh: bool | None = None, boom: bool = False):
        self._cached = cached
        self._after_refresh = after_refresh
        self._boom = boom
        self.refreshes = 0
        self.logs: list[str] = []
        self._ensure_update_availability = listener.KioskMqttListener._ensure_update_availability.__get__(self)

    def is_update_available(self) -> bool:
        return self._cached

    def refresh_update_availability(self) -> None:
        self.refreshes += 1
        if self._boom:
            raise RuntimeError("github unreachable")
        if self._after_refresh is not None:
            self._cached = self._after_refresh

    def log(self, message: str) -> None:
        self.logs.append(message)


def test_cached_availability_skips_the_extra_request(listener):
    s = _UpdateStub(listener, cached=True)
    assert s._ensure_update_availability() is True
    assert s.refreshes == 0  # must not spend a GitHub call when we already know


def test_stale_cache_is_refreshed_so_a_fresh_release_is_seen(listener):
    # The exact case that made the button look broken: a release cut minutes ago
    # is invisible to the cache for up to 2h.
    s = _UpdateStub(listener, cached=False, after_refresh=True)
    assert s._ensure_update_availability() is True
    assert s.refreshes == 1


def test_genuinely_no_update_still_returns_false(listener):
    s = _UpdateStub(listener, cached=False, after_refresh=False)
    assert s._ensure_update_availability() is False
    assert s.refreshes == 1


def test_failed_check_does_not_propagate(listener):
    # GitHub being unreachable must not turn a button press into a crash.
    s = _UpdateStub(listener, cached=False, boom=True)
    assert s._ensure_update_availability() is False
    assert s.refreshes == 1
    assert any("on-demand version check failed" in m for m in s.logs)
