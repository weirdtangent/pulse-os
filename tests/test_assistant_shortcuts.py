from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_SPEC = importlib.util.spec_from_file_location(
    "pulse_assistant_module", Path(__file__).resolve().parents[1] / "bin" / "pulse-assistant.py"
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = _MODULE
_MODULE_SPEC.loader.exec_module(_MODULE)  # type: ignore[attr-defined]
PulseAssistant = _MODULE.PulseAssistant  # type: ignore[attr-defined]


def _assistant() -> PulseAssistant:
    return object.__new__(PulseAssistant)  # type: ignore[misc]


def test_extract_time_of_day_handles_four_digit_am_pm() -> None:
    assistant = _assistant()
    result = assistant._extract_time_of_day_from_text("at 1225 pm tomorrow")
    assert result == "12:25"


def test_extract_time_of_day_handles_three_digit_am_pm() -> None:
    assistant = _assistant()
    result = assistant._extract_time_of_day_from_text("remind me at 725am")
    assert result == "07:25"


def test_conversation_stop_phrase_detection() -> None:
    assistant = _assistant()
    assistant._conversation_stop_prefixes = ("pulse", "hey pulse", "ok pulse", "okay pulse")
    assert assistant._is_conversation_stop_command("Nevermind.")
    assert assistant._is_conversation_stop_command("nothing else, thanks")
    assert assistant._is_conversation_stop_command("ok pulse you can stop please")
    assert not assistant._is_conversation_stop_command("cancel the alarm")
    assert not assistant._is_conversation_stop_command("stop the timer")


def test_conversation_stop_prefixes_follow_wake_words() -> None:
    assistant = _assistant()
    assistant._conversation_stop_prefixes = ("hey gizmo", "gizmo")
    assert assistant._is_conversation_stop_command("Hey Gizmo forget it")
    assistant._conversation_stop_prefixes = ("hey other",)
    assert not assistant._is_conversation_stop_command("Hey Gizmo forget it")
