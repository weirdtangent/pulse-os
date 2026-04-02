"""Unit tests for wake word endpoint routing."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest
from pulse.assistant.config import WyomingEndpoint
from pulse.assistant.wake_detector import WakeDetector, WakeEndpointStream, compute_rms


def _build_detector(
    wake_models: list[str],
    wake_routes: dict[str, str],
    ha_endpoint: WyomingEndpoint | None,
) -> WakeDetector:
    base_endpoint = WyomingEndpoint(host="pulse.local", port=10400)
    home_assistant = SimpleNamespace(wake_endpoint=ha_endpoint)
    mic = SimpleNamespace(rate=16000, width=2, channels=1, chunk_ms=30)
    preferences = SimpleNamespace(wake_sensitivity="normal")
    config = SimpleNamespace(
        wake_models=wake_models,
        wake_routes=wake_routes,
        wake_endpoint=base_endpoint,
        mic=mic,
        home_assistant=home_assistant,
    )
    return WakeDetector(config, preferences, mic, self_audio_trigger_level=5)  # type: ignore[arg-type]


def test_wake_endpoint_streams_routes_models_to_home_assistant():
    ha_endpoint = WyomingEndpoint(host="ha.local", port=20400)
    detector = _build_detector(
        ["hey_jarvis", "hey_house"],
        {"hey_jarvis": "pulse", "hey_house": "home_assistant"},
        ha_endpoint,
    )

    streams = detector._wake_endpoint_streams()

    assert len(streams) == 2
    pulse_stream = next(stream for stream in streams if "Pulse" in stream.display_label)
    ha_stream = next(stream for stream in streams if "Home Assistant" in stream.display_label)

    assert pulse_stream.models == ["hey_jarvis"]
    assert ha_stream.models == ["hey_house"]


def test_wake_endpoint_streams_fall_back_when_ha_endpoint_missing():
    detector = _build_detector(
        ["hey_jarvis", "hey_house"],
        {"hey_jarvis": "pulse", "hey_house": "home_assistant"},
        None,
    )

    streams = detector._wake_endpoint_streams()

    # Each model gets its own stream; when HA endpoint is missing, both fall back to Pulse
    assert len(streams) == 2
    all_models = {model for stream in streams for model in stream.models}
    assert all_models == {"hey_jarvis", "hey_house"}
    # Both should have "Pulse" label since HA endpoint is missing
    for stream in streams:
        assert "Pulse" in stream.labels


def test_wake_endpoint_streams_separate_per_model_even_when_endpoints_match():
    """Each model gets its own stream even when endpoints match.

    This is intentional to work around openWakeWord limitation where only
    the first model in a Detect message is loaded.
    """
    shared_endpoint = WyomingEndpoint(host="pulse.local", port=10400)
    detector = _build_detector(
        ["hey_jarvis", "hey_house"],
        {"hey_jarvis": "pulse", "hey_house": "home_assistant"},
        shared_endpoint,
    )

    streams = detector._wake_endpoint_streams()

    # Each model gets its own stream
    assert len(streams) == 2
    all_models = {model for stream in streams for model in stream.models}
    all_labels = {label for stream in streams for label in stream.labels}
    assert all_models == {"hey_jarvis", "hey_house"}
    assert all_labels == {"Pulse", "Home Assistant"}


# ---------------------------------------------------------------------------
# WakeEndpointStream.display_label
# ---------------------------------------------------------------------------


def test_display_label_single_label():
    stream = WakeEndpointStream(
        endpoint=WyomingEndpoint(host="host1", port=1234),
        labels={"Pulse"},
        models=["m1"],
    )
    assert stream.display_label == "Pulse (host1:1234)"


def test_display_label_multiple_labels_sorted():
    stream = WakeEndpointStream(
        endpoint=WyomingEndpoint(host="host1", port=1234),
        labels={"Zulu", "Alpha"},
        models=["m1"],
    )
    assert stream.display_label == "Alpha/Zulu (host1:1234)"


# ---------------------------------------------------------------------------
# compute_rms
# ---------------------------------------------------------------------------


def test_compute_rms_empty_chunk():
    assert compute_rms(b"", 2) == 0


def test_compute_rms_zero_sample_width():
    assert compute_rms(b"\x00\x00", 0) == 0


def test_compute_rms_negative_sample_width():
    assert compute_rms(b"\x00\x00", -1) == 0


def test_compute_rms_16bit_silence():
    # 4 silent 16-bit samples
    chunk = struct.pack("<4h", 0, 0, 0, 0)
    assert compute_rms(chunk, 2) == 0


def test_compute_rms_16bit_nonzero():
    # All samples at value 100 -> RMS should be 100
    chunk = struct.pack("<4h", 100, 100, 100, 100)
    assert compute_rms(chunk, 2) == 100


def test_compute_rms_8bit():
    # 8-bit signed: all at value 50
    chunk = struct.pack("4b", 50, 50, 50, 50)
    assert compute_rms(chunk, 1) == 50


def test_compute_rms_32bit():
    # 32-bit signed: all at value 1000
    chunk = struct.pack("<4i", 1000, 1000, 1000, 1000)
    assert compute_rms(chunk, 4) == 1000


def test_compute_rms_unusual_sample_width():
    # 3-byte samples (24-bit audio) — uses the fallback int.from_bytes path
    # Encode value 256 as 3-byte little-endian signed: 0x00 0x01 0x00
    val = 256
    sample = val.to_bytes(3, "little", signed=True)
    chunk = sample * 4
    assert compute_rms(chunk, 3) == 256


def test_compute_rms_truncates_partial_frame():
    # 5 bytes with sample_width=2 -> only 2 full frames (4 bytes used)
    chunk = struct.pack("<2h", 200, 200) + b"\xff"
    assert compute_rms(chunk, 2) == 200


# ---------------------------------------------------------------------------
# WakeDetector.__init__
# ---------------------------------------------------------------------------


def _make_detector(
    sensitivity: str = "normal",
    trigger_level: int = 5,
    wake_models: list[str] | None = None,
    ha_endpoint: WyomingEndpoint | None = None,
) -> WakeDetector:
    """Helper that builds a WakeDetector with sensible defaults."""
    base_endpoint = WyomingEndpoint(host="pulse.local", port=10400)
    home_assistant = SimpleNamespace(wake_endpoint=ha_endpoint)
    mic = SimpleNamespace(rate=16000, width=2, channels=1, chunk_ms=80)
    preferences = SimpleNamespace(wake_sensitivity=sensitivity)
    config = SimpleNamespace(
        wake_models=wake_models if wake_models is not None else ["hey_pulse"],
        wake_routes={},
        wake_endpoint=base_endpoint,
        mic=mic,
        home_assistant=home_assistant,
    )
    return WakeDetector(config, preferences, mic, self_audio_trigger_level=trigger_level)  # type: ignore[arg-type]


def test_init_sets_attributes():
    d = _make_detector(trigger_level=7)
    assert d._self_audio_trigger_level == 7
    assert d._local_audio_depth == 0
    assert d._self_audio_remote_active is False
    assert d._wake_context_version == 0


# ---------------------------------------------------------------------------
# self_audio_is_active
# ---------------------------------------------------------------------------


def test_self_audio_not_active_by_default():
    d = _make_detector()
    assert d.self_audio_is_active() is False


def test_self_audio_active_when_local_depth_positive():
    d = _make_detector()
    d.increment_local_audio_depth()
    assert d.self_audio_is_active() is True


def test_self_audio_active_when_remote_active():
    d = _make_detector()
    d.set_remote_audio_active(True)
    assert d.self_audio_is_active() is True


# ---------------------------------------------------------------------------
# get/set_remote_audio_active
# ---------------------------------------------------------------------------


def test_get_remote_audio_active_default():
    d = _make_detector()
    assert d.get_remote_audio_active() is False


def test_set_remote_audio_active_returns_true_on_change():
    d = _make_detector()
    assert d.set_remote_audio_active(True) is True
    assert d.get_remote_audio_active() is True


def test_set_remote_audio_active_returns_false_on_no_change():
    d = _make_detector()
    assert d.set_remote_audio_active(False) is False


def test_set_remote_audio_active_toggle():
    d = _make_detector()
    d.set_remote_audio_active(True)
    assert d.set_remote_audio_active(False) is True
    assert d.get_remote_audio_active() is False


# ---------------------------------------------------------------------------
# increment/decrement_local_audio_depth
# ---------------------------------------------------------------------------


def test_increment_local_audio_depth_marks_dirty_on_first():
    d = _make_detector()
    v0 = d._wake_context_version
    d.increment_local_audio_depth()
    assert d._wake_context_version == (v0 + 1) % 1_000_000


def test_increment_local_audio_depth_no_dirty_on_second():
    d = _make_detector()
    d.increment_local_audio_depth()
    v1 = d._wake_context_version
    d.increment_local_audio_depth()
    assert d._wake_context_version == v1  # no change


def test_decrement_local_audio_depth_marks_dirty_on_zero():
    d = _make_detector()
    d.increment_local_audio_depth()
    d.increment_local_audio_depth()
    v = d._wake_context_version
    d.decrement_local_audio_depth()  # depth 2->1, no notify
    assert d._wake_context_version == v
    d.decrement_local_audio_depth()  # depth 1->0, notify
    assert d._wake_context_version == v + 1


def test_decrement_local_audio_depth_no_underflow():
    d = _make_detector()
    d.decrement_local_audio_depth()  # already 0, should not underflow
    assert d._local_audio_depth == 0


# ---------------------------------------------------------------------------
# mark_wake_context_dirty
# ---------------------------------------------------------------------------


def test_mark_wake_context_dirty_increments_version():
    d = _make_detector()
    v0 = d._wake_context_version
    d.mark_wake_context_dirty()
    assert d._wake_context_version == v0 + 1


def test_mark_wake_context_dirty_wraps():
    d = _make_detector()
    d._wake_context_version = 999_999
    d.mark_wake_context_dirty()
    assert d._wake_context_version == 0


# ---------------------------------------------------------------------------
# local_audio_block (async context manager)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_local_audio_block_increments_and_decrements():
    d = _make_detector()
    assert d._local_audio_depth == 0
    async with d.local_audio_block():
        assert d._local_audio_depth == 1
    assert d._local_audio_depth == 0


@pytest.mark.anyio
async def test_local_audio_block_decrements_on_exception():
    d = _make_detector()
    with pytest.raises(RuntimeError):
        async with d.local_audio_block():
            raise RuntimeError("boom")
    assert d._local_audio_depth == 0


# ---------------------------------------------------------------------------
# _preferred_trigger_level
# ---------------------------------------------------------------------------


def test_preferred_trigger_level_low():
    d = _make_detector(sensitivity="low")
    assert d._preferred_trigger_level() == 5


def test_preferred_trigger_level_high():
    d = _make_detector(sensitivity="high")
    assert d._preferred_trigger_level() == 2


def test_preferred_trigger_level_default():
    d = _make_detector(sensitivity="normal")
    assert d._preferred_trigger_level() is None


def test_preferred_trigger_level_unknown_value():
    d = _make_detector(sensitivity="medium")
    assert d._preferred_trigger_level() is None


# ---------------------------------------------------------------------------
# _context_for_detect
# ---------------------------------------------------------------------------


def test_context_for_detect_no_audio_default_sensitivity():
    d = _make_detector(sensitivity="normal")
    assert d._context_for_detect() is None


def test_context_for_detect_no_audio_low_sensitivity():
    d = _make_detector(sensitivity="low")
    assert d._context_for_detect() == {"trigger_level": 5}


def test_context_for_detect_no_audio_high_sensitivity():
    d = _make_detector(sensitivity="high")
    assert d._context_for_detect() == {"trigger_level": 2}


def test_context_for_detect_audio_active_default_sensitivity():
    d = _make_detector(sensitivity="normal", trigger_level=7)
    d.increment_local_audio_depth()
    # No preferred trigger level -> uses self_audio_trigger_level
    assert d._context_for_detect() == {"trigger_level": 7}


def test_context_for_detect_audio_active_low_sensitivity():
    # low -> 5, self_audio -> 7, max(5,7) = 7
    d = _make_detector(sensitivity="low", trigger_level=7)
    d.increment_local_audio_depth()
    assert d._context_for_detect() == {"trigger_level": 7}


def test_context_for_detect_audio_active_high_sensitivity_below_self():
    # high -> 2, self_audio -> 7, max(2,7) = 7
    d = _make_detector(sensitivity="high", trigger_level=7)
    d.set_remote_audio_active(True)
    assert d._context_for_detect() == {"trigger_level": 7}


def test_context_for_detect_audio_active_self_below_preferred():
    # low -> 5, self_audio -> 3, max(5,3) = 5
    d = _make_detector(sensitivity="low", trigger_level=3)
    d.increment_local_audio_depth()
    assert d._context_for_detect() == {"trigger_level": 5}


# ---------------------------------------------------------------------------
# _wake_endpoint_streams — pulse-only models
# ---------------------------------------------------------------------------


def test_wake_endpoint_streams_single_pulse_model():
    d = _make_detector(wake_models=["hey_pulse"])
    streams = d._wake_endpoint_streams()
    assert len(streams) == 1
    assert streams[0].models == ["hey_pulse"]
    assert "Pulse" in streams[0].labels


def test_wake_endpoint_streams_no_models():
    d = _make_detector(wake_models=[])
    assert d._wake_endpoint_streams() == []


# ---------------------------------------------------------------------------
# stable_detect_context
# ---------------------------------------------------------------------------


def test_stable_detect_context_returns_context_and_version():
    d = _make_detector(sensitivity="low")
    ctx, ver = d.stable_detect_context()
    assert ctx == {"trigger_level": 5}
    assert ver == 0


def test_stable_detect_context_none_when_default():
    d = _make_detector(sensitivity="normal")
    ctx, ver = d.stable_detect_context()
    assert ctx is None
    assert ver == 0


# ---------------------------------------------------------------------------
# _debug_throttled
# ---------------------------------------------------------------------------


def test_debug_throttled_logs_first_call(caplog):
    import logging

    d = _make_detector()
    with caplog.at_level(logging.DEBUG, logger="pulse-assistant.wake"):
        d._debug_throttled("key1", "hello %s", "world", interval=30.0)
    assert "hello world" in caplog.text


def test_debug_throttled_suppresses_within_interval(caplog):
    import logging

    d = _make_detector()
    with caplog.at_level(logging.DEBUG, logger="pulse-assistant.wake"):
        d._debug_throttled("key2", "first", interval=30.0)
        caplog.clear()
        d._debug_throttled("key2", "second", interval=30.0)
    assert "second" not in caplog.text


def test_debug_throttled_allows_after_interval(caplog):
    import logging

    d = _make_detector()
    with caplog.at_level(logging.DEBUG, logger="pulse-assistant.wake"):
        d._debug_throttled("key3", "first", interval=0.0)
        caplog.clear()
        d._debug_throttled("key3", "second", interval=0.0)
    assert "second" in caplog.text


def test_debug_throttled_different_keys_independent(caplog):
    import logging

    d = _make_detector()
    with caplog.at_level(logging.DEBUG, logger="pulse-assistant.wake"):
        d._debug_throttled("a", "msg_a", interval=30.0)
        d._debug_throttled("b", "msg_b", interval=30.0)
    assert "msg_a" in caplog.text
    assert "msg_b" in caplog.text
