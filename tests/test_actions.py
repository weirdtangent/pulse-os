from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from pulse.assistant import actions


@contextmanager
def _frozen_now(reference: datetime):
    with patch("pulse.assistant.actions._local_now", return_value=reference):
        with patch("pulse.assistant.actions._utc_now", return_value=reference):
            yield


def test_parse_datetime_today_with_noon_phrase() -> None:
    reference = datetime(2025, 11, 24, 8, 0, tzinfo=UTC)
    with _frozen_now(reference):
        result = actions._parse_datetime("today at noon every week")
    assert result == reference.replace(hour=12, minute=0, second=0, microsecond=0)


def test_parse_datetime_tomorrow_specific_time() -> None:
    reference = datetime(2025, 11, 24, 8, 0, tzinfo=UTC)
    with _frozen_now(reference):
        result = actions._parse_datetime("tomorrow 6:30pm")
    expected = (reference + timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)
    assert result == expected


def test_parse_datetime_today_defaults_when_no_time_given() -> None:
    reference = datetime(2025, 11, 24, 8, 0, tzinfo=UTC)
    with _frozen_now(reference):
        result = actions._parse_datetime("today")
    assert result == reference.replace(hour=9, minute=0, second=0, microsecond=0)


def test_parse_datetime_every_monday_at_noon_future_same_day() -> None:
    reference = datetime(2025, 11, 24, 8, 0, tzinfo=UTC)  # Monday
    with _frozen_now(reference):
        result = actions._parse_datetime("every Monday at noon to bring trash in")
    assert result == reference.replace(hour=12, minute=0, second=0, microsecond=0)


def test_parse_datetime_next_monday_when_time_passed() -> None:
    reference = datetime(2025, 11, 24, 15, 0, tzinfo=UTC)  # Monday afternoon
    with _frozen_now(reference):
        result = actions._parse_datetime("next Monday at 9am")
    expected = reference + timedelta(days=7)
    expected = expected.replace(hour=9, minute=0, second=0, microsecond=0)
    assert result == expected


def test_parse_brightness_and_color_temp_helpers() -> None:
    brightness = actions._parse_brightness_pct({"brightness": "75%"})
    color_mired = actions._parse_color_temp_mired({"kelvin": "2700"})
    transition = actions._parse_transition_seconds({"transition": "1.5"})
    assert brightness == 75.0
    assert color_mired == actions.kelvin_to_mired(2700)
    assert transition == 1.5
