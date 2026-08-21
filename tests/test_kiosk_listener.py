"""Tests for helpers in bin/kiosk-mqtt-listener.py.

The listener is a script rather than a package module, so it is loaded by path. Only pure
helpers are exercised here — nothing that touches MQTT, audio, or the display.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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
