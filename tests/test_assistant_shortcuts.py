from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pulse.assistant.calendar_sync import CalendarReminder
from pulse.assistant.conversation_manager import (
    evaluate_follow_up_transcript,
    is_conversation_stop_command,
)
from pulse.assistant.schedule_intents import ScheduleIntentParser

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_MODULE_SPEC = importlib.util.spec_from_file_location(
    "pulse_assistant_module", Path(__file__).resolve().parents[1] / "bin" / "pulse-assistant.py"
)
assert _MODULE_SPEC and _MODULE_SPEC.loader
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = _MODULE
_MODULE_SPEC.loader.exec_module(_MODULE)  # type: ignore[attr-defined]
PulseAssistant: Any = _MODULE.PulseAssistant  # type: ignore[attr-defined]


def _assistant() -> Any:
    return object.__new__(PulseAssistant)  # type: ignore[misc]


def test_extract_time_of_day_handles_four_digit_am_pm() -> None:
    result = ScheduleIntentParser._extract_time_of_day_from_text("at 1225 pm tomorrow")
    assert result == "12:25"


def test_extract_time_of_day_handles_three_digit_am_pm() -> None:
    result = ScheduleIntentParser._extract_time_of_day_from_text("remind me at 725am")
    assert result == "07:25"


def test_conversation_stop_phrase_detection() -> None:
    prefixes = ("pulse", "hey pulse", "ok pulse", "okay pulse")
    assert is_conversation_stop_command("Nevermind.", prefixes)
    assert is_conversation_stop_command("nothing else, thanks", prefixes)
    assert is_conversation_stop_command("ok pulse you can stop please", prefixes)
    assert not is_conversation_stop_command("cancel the alarm", prefixes)
    assert not is_conversation_stop_command("stop the timer", prefixes)


def test_conversation_stop_prefixes_follow_wake_words() -> None:
    prefixes = ("hey gizmo", "gizmo")
    assert is_conversation_stop_command("Hey Gizmo forget it", prefixes)
    other_prefixes = ("hey other",)
    assert not is_conversation_stop_command("Hey Gizmo forget it", other_prefixes)


def test_follow_up_noise_filtering() -> None:
    ok, normalized = evaluate_follow_up_transcript("You", None)
    assert not ok and normalized == "you"
    ok, normalized = evaluate_follow_up_transcript("Add tomatoes", None)
    assert ok and normalized == "add tomatoes"
    ok, normalized = evaluate_follow_up_transcript("Add tomatoes", normalized)
    assert not ok


def _make_calendar_manager() -> Any:
    from unittest.mock import AsyncMock, Mock

    from pulse.assistant.calendar_manager import CalendarEventManager

    service = Mock()
    service.set_ooo_skip_dates = AsyncMock()
    return CalendarEventManager(
        schedule_service=service,
        ooo_summary_marker="OOO",
        calendar_enabled=True,
        calendar_has_feeds=True,
    )


def test_calendar_snapshot_deduplicates_multiple_valarms() -> None:
    mgr = _make_calendar_manager()
    # Use a date far in the future to avoid filtering
    start = datetime.now(UTC) + timedelta(days=30)
    reminders = [
        CalendarReminder(
            uid="event-1",
            summary="Team sync",
            description=None,
            location=None,
            start=start,
            end=None,
            all_day=False,
            trigger_time=start - timedelta(minutes=30),
            calendar_name="Work",
            source_url="https://example.com/work.ics",
            url=None,
        ),
        CalendarReminder(
            uid="event-1",
            summary="Team sync",
            description=None,
            location=None,
            start=start,
            end=None,
            all_day=False,
            trigger_time=start - timedelta(minutes=5),
            calendar_name="Work",
            source_url="https://example.com/work.ics",
            url=None,
        ),
    ]
    asyncio.run(mgr.handle_calendar_snapshot(reminders))
    assert len(mgr.calendar_events) == 1
    assert mgr.calendar_events[0]["summary"] == "Team sync"


def test_calendar_snapshot_retains_distinct_sources() -> None:
    mgr = _make_calendar_manager()
    # Use a date far in the future to avoid filtering
    start = datetime.now(UTC) + timedelta(days=30)
    reminders = [
        CalendarReminder(
            uid="event-shared",
            summary="Project kickoff",
            description=None,
            location=None,
            start=start,
            end=None,
            all_day=False,
            trigger_time=start - timedelta(minutes=15),
            calendar_name="Work",
            source_url="https://example.com/work.ics",
            url=None,
        ),
        CalendarReminder(
            uid="event-shared",
            summary="Project kickoff",
            description=None,
            location=None,
            start=start,
            end=None,
            all_day=False,
            trigger_time=start - timedelta(minutes=10),
            calendar_name="Personal",
            source_url="https://example.com/personal.ics",
            url=None,
        ),
    ]
    asyncio.run(mgr.handle_calendar_snapshot(reminders))
    assert len(mgr.calendar_events) == 2
