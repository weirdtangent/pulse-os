"""Unit tests for wake word endpoint routing."""

from __future__ import annotations

from types import SimpleNamespace

from pulse.assistant.config import WyomingEndpoint
from pulse.assistant.wake_detector import WakeDetector


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
    return WakeDetector(config, preferences, mic, self_audio_trigger_level=5)


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
