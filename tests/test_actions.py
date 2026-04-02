from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from unittest.mock import patch as mock_patch

import pytest
from pulse.assistant import actions
from pulse.assistant.actions import (
    ActionDefinition,
    ActionEngine,
    HomeAssistantError,
    _color_name_to_rgb,
    _duration_from_args,
    _ensure_list,
    _entity_domain,
    _execute_light_action,
    _maybe_execute_home_assistant_action,
    _maybe_execute_media_action,
    _maybe_execute_scheduler_action,
    _parse_action_args,
    _parse_brightness_pct,
    _parse_color_temp_mired,
    _parse_percentage,
    _parse_rgb_color,
    _parse_transition_seconds,
    _playback_from_args,
    _preferred_domains,
    _reminder_repeat_from_args,
    _resolve_entities,
    _resolve_schedule_event_id,
    _split_action_token,
    load_action_definitions,
)


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


# ===== _ensure_list =====


def test_ensure_list_with_list_of_dicts():
    assert _ensure_list([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_ensure_list_filters_non_dicts():
    assert _ensure_list([{"a": 1}, "nope", 42]) == [{"a": 1}]


def test_ensure_list_with_single_dict():
    assert _ensure_list({"key": "val"}) == [{"key": "val"}]


def test_ensure_list_with_string():
    assert _ensure_list("hello") == []


def test_ensure_list_with_none():
    assert _ensure_list(None) == []


# ===== load_action_definitions =====


def test_load_action_definitions_from_file():
    data = [
        {"slug": "test.action", "topic": "test/topic", "payload": "ON", "description": "Test"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        result = load_action_definitions(Path(f.name), None)
    assert len(result) == 1
    assert result[0].slug == "test.action"
    assert result[0].topic == "test/topic"
    assert result[0].payload == "ON"
    assert result[0].type == "mqtt"


def test_load_action_definitions_from_inline_json():
    inline = json.dumps({"slug": "inline.action", "topic": "t", "payload": {"k": "v"}})
    result = load_action_definitions(None, inline)
    assert len(result) == 1
    assert result[0].slug == "inline.action"
    assert result[0].payload == '{"k": "v"}'


def test_load_action_definitions_skips_missing_fields():
    inline = json.dumps([{"slug": "no_topic", "payload": "x"}, {"slug": "", "topic": "t", "payload": "x"}])
    result = load_action_definitions(None, inline)
    assert len(result) == 0


def test_load_action_definitions_skips_non_mqtt_type():
    inline = json.dumps({"slug": "s", "topic": "t", "payload": "p", "type": "http"})
    result = load_action_definitions(None, inline)
    assert len(result) == 0


def test_load_action_definitions_nonexistent_file():
    result = load_action_definitions(Path("/nonexistent/path.json"), None)
    assert result == []


def test_load_action_definitions_invalid_json():
    result = load_action_definitions(None, "not valid json{{")
    assert result == []


def test_load_action_definitions_combined_file_and_inline():
    file_data = [{"slug": "file.one", "topic": "t1", "payload": "p1"}]
    inline_data = json.dumps({"slug": "inline.two", "topic": "t2", "payload": "p2"})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(file_data, f)
        f.flush()
        result = load_action_definitions(Path(f.name), inline_data)
    assert len(result) == 2
    slugs = {r.slug for r in result}
    assert slugs == {"file.one", "inline.two"}


# ===== ActionDefinition.to_prompt_dict =====


def test_action_definition_to_prompt_dict():
    ad = ActionDefinition(slug="my.slug", description="My desc", type="mqtt", topic="t", payload="p")
    assert ad.to_prompt_dict() == {"slug": "my.slug", "description": "My desc"}


# ===== ActionEngine =====


def test_action_engine_describe_for_prompt():
    defs = [
        ActionDefinition(slug="a", description="A", type="mqtt", topic="t", payload="p"),
        ActionDefinition(slug="b", description="B", type="mqtt", topic="t2", payload="p2"),
    ]
    engine = ActionEngine(defs)
    prompts = engine.describe_for_prompt()
    assert len(prompts) == 2
    assert prompts[0] == {"slug": "a", "description": "A"}


@pytest.mark.anyio
async def test_action_engine_execute_mqtt():
    defn = ActionDefinition(
        slug="my.mqtt", description="d", type="mqtt", topic="out/topic", payload="ON", retain=True, qos=1
    )
    engine = ActionEngine([defn])
    mqtt = Mock()
    result = await engine.execute(["my.mqtt"], mqtt)
    assert result == ["my.mqtt"]
    mqtt.publish.assert_called_once_with("out/topic", "ON", retain=True, qos=1)


@pytest.mark.anyio
async def test_action_engine_execute_dedup():
    defn = ActionDefinition(slug="d", description="d", type="mqtt", topic="t", payload="p")
    engine = ActionEngine([defn])
    mqtt = Mock()
    result = await engine.execute(["d", "d", "d"], mqtt)
    assert result == ["d"]
    assert mqtt.publish.call_count == 1


@pytest.mark.anyio
async def test_action_engine_execute_unknown_slug_delegates_to_ha():
    engine = ActionEngine([])
    mqtt = Mock()
    ha = AsyncMock()
    ha.call_service = AsyncMock()
    result = await engine.execute(["ha.turn_on: entity_id=switch.lamp"], mqtt, ha_client=ha)
    assert result == ["ha.turn_on"]


@pytest.mark.anyio
async def test_action_engine_execute_empty_token_skipped():
    engine = ActionEngine([])
    mqtt = Mock()
    result = await engine.execute(["", "  "], mqtt)
    assert result == []


# ===== _split_action_token =====


def test_split_action_token_with_colon():
    assert _split_action_token("ha.turn_on: entity_id=light.foo") == ("ha.turn_on", "entity_id=light.foo")


def test_split_action_token_no_colon():
    assert _split_action_token("ha.turn_on") == ("ha.turn_on", "")


def test_split_action_token_empty():
    assert _split_action_token("") == ("", "")


def test_split_action_token_whitespace():
    assert _split_action_token("  slug : args  ") == ("slug", "args")


# ===== _parse_action_args =====


def test_parse_action_args_key_value():
    assert _parse_action_args("brightness=50, room=bedroom") == {"brightness": "50", "room": "bedroom"}


def test_parse_action_args_bare_entity():
    assert _parse_action_args("light.kitchen") == {"entity_id": "light.kitchen"}


def test_parse_action_args_empty():
    assert _parse_action_args("") == {}


def test_parse_action_args_mixed():
    result = _parse_action_args("light.foo, brightness=75")
    assert result["entity_id"] == "light.foo"
    assert result["brightness"] == "75"


def test_parse_action_args_ignores_phrases_with_spaces():
    result = _parse_action_args("turn on the light, room=kitchen")
    assert "entity_id" not in result
    assert result["room"] == "kitchen"


# ===== _entity_domain =====


def test_entity_domain_light():
    assert _entity_domain("light.kitchen") == "light"


def test_entity_domain_switch():
    assert _entity_domain("switch.fan") == "switch"


def test_entity_domain_no_dot():
    assert _entity_domain("nodomain") == ""


def test_entity_domain_none():
    assert _entity_domain(None) == ""


def test_entity_domain_empty():
    assert _entity_domain("") == ""


# ===== _parse_brightness_pct edge cases =====


def test_parse_brightness_no_value():
    assert _parse_brightness_pct({}) is None


def test_parse_brightness_invalid():
    assert _parse_brightness_pct({"brightness": "notanumber"}) is None


def test_parse_brightness_clamps_high():
    assert _parse_brightness_pct({"brightness": "150"}) == 100.0


def test_parse_brightness_clamps_low():
    assert _parse_brightness_pct({"brightness": "-10"}) == 0.0


def test_parse_brightness_level_key():
    assert _parse_brightness_pct({"level": "50%"}) == 50.0


# ===== _parse_color_temp_mired =====


def test_parse_color_temp_mired_kelvin_value():
    result = _parse_color_temp_mired({"kelvin": "4000"})
    assert result == actions.kelvin_to_mired(4000)


def test_parse_color_temp_mired_low_value_is_mired():
    result = _parse_color_temp_mired({"color_temp": "250"})
    assert result == 250


def test_parse_color_temp_mired_empty_string():
    assert _parse_color_temp_mired({"color_temp": ""}) is None


def test_parse_color_temp_mired_invalid():
    assert _parse_color_temp_mired({"color_temp": "warm"}) is None


def test_parse_color_temp_mired_none():
    assert _parse_color_temp_mired({}) is None


def test_parse_color_temp_mired_kelvin_suffix():
    result = _parse_color_temp_mired({"kelvin": "3000k"})
    assert result == actions.kelvin_to_mired(3000)


# ===== _parse_rgb_color =====


def test_parse_rgb_color_tuple():
    assert _parse_rgb_color({"rgb": "255,0,128"}) == (255, 0, 128)


def test_parse_rgb_color_bracketed():
    assert _parse_rgb_color({"rgb_color": "[0,255,0]"}) == (0, 255, 0)


def test_parse_rgb_color_hex_with_hash():
    assert _parse_rgb_color({"color": "#ff0000"}) == (255, 0, 0)


def test_parse_rgb_color_hex_without_hash():
    assert _parse_rgb_color({"hex": "00ff00"}) == (0, 255, 0)


def test_parse_rgb_color_hex_8_char():
    # 8-char hex: first 2 chars treated as alpha, last 6 as RGB
    assert _parse_rgb_color({"hex": "ff0000ff"}) == (0, 0, 255)


def test_parse_rgb_color_name():
    assert _parse_rgb_color({"color": "red"}) == (255, 0, 0)


def test_parse_rgb_color_colour_key():
    assert _parse_rgb_color({"colour": "blue"}) == (0, 0, 255)


def test_parse_rgb_color_none():
    assert _parse_rgb_color({}) is None


def test_parse_rgb_color_invalid_tuple():
    assert _parse_rgb_color({"rgb": "not,a,tuple"}) is None


def test_parse_rgb_color_clamps_values():
    result = _parse_rgb_color({"rgb": "300,-5,128"})
    assert result == (255, 0, 128)


# ===== _color_name_to_rgb =====


def test_color_name_known():
    assert _color_name_to_rgb("red") == (255, 0, 0)
    assert _color_name_to_rgb("warm") == (255, 147, 41)
    assert _color_name_to_rgb("grey") == (128, 128, 128)


def test_color_name_unknown():
    assert _color_name_to_rgb("chartreuse") is None


# ===== _parse_percentage =====


def test_parse_percentage_valid():
    assert _parse_percentage({"percentage": "75"}) == 75


def test_parse_percentage_speed_key():
    assert _parse_percentage({"speed": "50.5"}) == 50


def test_parse_percentage_clamps():
    assert _parse_percentage({"percentage": "200"}) == 100
    assert _parse_percentage({"percentage": "-10"}) == 0


def test_parse_percentage_invalid():
    assert _parse_percentage({"percentage": "fast"}) is None


def test_parse_percentage_empty():
    assert _parse_percentage({}) is None


# ===== _parse_transition_seconds =====


def test_parse_transition_valid():
    assert _parse_transition_seconds({"transition": "2.5"}) == 2.5


def test_parse_transition_fade_key():
    assert _parse_transition_seconds({"fade": "1.0"}) == 1.0


def test_parse_transition_invalid():
    assert _parse_transition_seconds({"transition": "slow"}) is None


def test_parse_transition_negative_clamps():
    assert _parse_transition_seconds({"transition": "-1"}) == 0.0


def test_parse_transition_empty():
    assert _parse_transition_seconds({}) is None


# ===== _preferred_domains =====


def test_preferred_domains_fan_hint_by_percentage():
    result = _preferred_domains({"percentage": "50"})
    assert result[0] == "fan"


def test_preferred_domains_fan_hint_by_name():
    result = _preferred_domains({"name": "ceiling fan"})
    assert result[0] == "fan"


def test_preferred_domains_color_hint():
    result = _preferred_domains({"color": "red"})
    assert result[0] == "light"


def test_preferred_domains_default():
    result = _preferred_domains({})
    assert result[0] == "light"


# ===== _resolve_entities =====


@pytest.mark.anyio
async def test_resolve_entities_with_entity_id():
    ha = AsyncMock()
    result = await _resolve_entities({"entity_id": "light.kitchen"}, ha)
    assert result == ["light.kitchen"]
    ha.list_entities.assert_not_called()


@pytest.mark.anyio
async def test_resolve_entities_scope_all():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.a", "attributes": {}},
        {"entity_id": "light.b", "attributes": {}},
    ]
    result = await _resolve_entities({"all": "true"}, ha, "light")
    assert result == ["light.a", "light.b"]


@pytest.mark.anyio
async def test_resolve_entities_by_name_hint():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {
            "entity_id": "light.kitchen_main",
            "attributes": {"friendly_name": "Kitchen Main Light", "area_id": "kitchen"},
        },
        {"entity_id": "light.bedroom", "attributes": {"friendly_name": "Bedroom Light", "area_id": "bedroom"}},
    ]
    result = await _resolve_entities({"name": "kitchen"}, ha, "light")
    assert result == ["light.kitchen_main"]


@pytest.mark.anyio
async def test_resolve_entities_by_room_hint():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.living_room", "attributes": {"friendly_name": "Light", "area_id": "living_room"}},
        {"entity_id": "light.bedroom", "attributes": {"friendly_name": "Light", "area_id": "bedroom"}},
    ]
    result = await _resolve_entities({"room": "living room"}, ha, "light")
    assert result == ["light.living_room"]


@pytest.mark.anyio
async def test_resolve_entities_no_hints_returns_empty():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.a", "attributes": {}},
    ]
    result = await _resolve_entities({}, ha, "light")
    assert result == []


@pytest.mark.anyio
async def test_resolve_entities_fallback_substring_match():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.xyzzy_thing", "attributes": {"friendly_name": "Xyzzy", "area_id": ""}},
    ]
    # name_hint "xyzzy" should match via fallback substring
    result = await _resolve_entities({"name": "xyzzy"}, ha, "light")
    assert result == ["light.xyzzy_thing"]


# ===== _maybe_execute_home_assistant_action =====


@pytest.mark.anyio
async def test_ha_action_none_client():
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=light.x", None)
    assert result is False


@pytest.mark.anyio
async def test_ha_turn_on_light_entity():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=light.kitchen, brightness=80", ha)
    assert result is True
    ha.set_light_state.assert_called_once()
    call_kwargs = ha.set_light_state.call_args
    assert call_kwargs[1]["on"] is True or call_kwargs[0][1] is True


@pytest.mark.anyio
async def test_ha_turn_on_switch_entity():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=switch.lamp", ha)
    assert result is True
    ha.call_service.assert_called_once_with("homeassistant", "turn_on", {"entity_id": "switch.lamp"})


@pytest.mark.anyio
async def test_ha_turn_off_light_entity():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_off", "entity_id=light.bedroom, transition=2", ha)
    assert result is True
    ha.set_light_state.assert_called_once()


@pytest.mark.anyio
async def test_ha_turn_off_switch_entity():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_off", "entity_id=switch.tv", ha)
    assert result is True
    ha.call_service.assert_called_once_with("homeassistant", "turn_off", {"entity_id": "switch.tv"})


@pytest.mark.anyio
async def test_ha_turn_on_fan_with_percentage():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=fan.ceiling, percentage=75", ha)
    assert result is True
    ha.call_service.assert_called_once_with("fan", "set_percentage", {"entity_id": "fan.ceiling", "percentage": 75})


@pytest.mark.anyio
async def test_ha_turn_on_fan_percentage_fallback_to_set_speed():
    ha = AsyncMock()
    ha.call_service = AsyncMock(side_effect=[HomeAssistantError("fail"), AsyncMock()])
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=fan.old, percentage=50", ha)
    assert result is True
    assert ha.call_service.call_count == 2
    second_call = ha.call_service.call_args_list[1]
    assert second_call[0][1] == "set_speed"


@pytest.mark.anyio
async def test_ha_turn_on_fan_percentage_fallback_to_generic():
    ha = AsyncMock()
    ha.call_service = AsyncMock(side_effect=[HomeAssistantError("a"), HomeAssistantError("b"), AsyncMock()])
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=fan.ancient, percentage=50", ha)
    assert result is True
    assert ha.call_service.call_count == 3


@pytest.mark.anyio
async def test_ha_turn_on_fan_no_percentage():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "entity_id=fan.simple", ha)
    assert result is True
    ha.call_service.assert_called_once_with("homeassistant", "turn_on", {"entity_id": "fan.simple"})


@pytest.mark.anyio
async def test_ha_turn_off_fan_with_percentage():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_off", "entity_id=fan.ceiling, percentage=25", ha)
    assert result is True
    ha.call_service.assert_called_once_with("fan", "set_percentage", {"entity_id": "fan.ceiling", "percentage": 25})


@pytest.mark.anyio
async def test_ha_turn_off_fan_no_percentage():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.turn_off", "entity_id=fan.ceiling", ha)
    assert result is True
    ha.call_service.assert_called_once_with("homeassistant", "turn_off", {"entity_id": "fan.ceiling"})


@pytest.mark.anyio
async def test_ha_scene_with_entity_id():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.scene", "entity_id=scene.movie_time", ha)
    assert result is True
    ha.activate_scene.assert_called_once_with("scene.movie_time")


@pytest.mark.anyio
async def test_ha_scene_with_name_prefix():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.scene", "name=movie_time", ha)
    assert result is True
    ha.activate_scene.assert_called_once_with("scene.movie_time")


@pytest.mark.anyio
async def test_ha_scene_no_scene_id():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.scene", "", ha)
    assert result is False


@pytest.mark.anyio
async def test_ha_unknown_slug():
    ha = AsyncMock()
    result = await _maybe_execute_home_assistant_action("ha.unknown", "", ha)
    assert result is False


@pytest.mark.anyio
async def test_ha_turn_on_resolve_by_name_light():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen Light", "area_id": "kitchen"}},
    ]
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "name=kitchen, brightness=50", ha)
    assert result is True
    ha.set_light_state.assert_called_once()


@pytest.mark.anyio
async def test_ha_turn_on_resolve_no_match():
    ha = AsyncMock()
    ha.list_entities.return_value = []
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "name=nonexistent", ha)
    assert result is False


@pytest.mark.anyio
async def test_ha_turn_off_resolve_by_name_light():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.bedroom", "attributes": {"friendly_name": "Bedroom Light", "area_id": "bedroom"}},
    ]
    result = await _maybe_execute_home_assistant_action("ha.turn_off", "name=bedroom", ha)
    assert result is True
    ha.set_light_state.assert_called_once()


@pytest.mark.anyio
async def test_ha_turn_off_resolve_fan_by_name():
    ha = AsyncMock()
    # First domain (light) returns nothing, second (fan) returns a match
    ha.list_entities.side_effect = [
        [],  # light domain
        [{"entity_id": "fan.bedroom", "attributes": {"friendly_name": "Bedroom Fan", "area_id": "bedroom"}}],
        [],  # switch
        [],  # None
    ]
    result = await _maybe_execute_home_assistant_action("ha.turn_off", "name=bedroom", ha)
    assert result is True
    ha.call_service.assert_called_once_with("homeassistant", "turn_off", {"entity_id": "fan.bedroom"})


@pytest.mark.anyio
async def test_ha_turn_on_resolve_fan_with_percentage():
    ha = AsyncMock()
    ha.list_entities.side_effect = [
        [],  # light
        [{"entity_id": "fan.living", "attributes": {"friendly_name": "Living Fan", "area_id": "living"}}],
    ]
    result = await _maybe_execute_home_assistant_action("ha.turn_on", "name=living, percentage=60", ha)
    assert result is True
    ha.call_service.assert_called_once_with("fan", "set_percentage", {"entity_id": "fan.living", "percentage": 60})


# ===== _execute_light_action =====


@pytest.mark.anyio
async def test_execute_light_action_on():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.desk", "attributes": {"friendly_name": "Desk Light", "area_id": "office"}},
    ]
    result = await _execute_light_action("ha.light_on", {"name": "desk"}, ha)
    assert result is True
    ha.set_light_state.assert_called_once()
    kwargs = ha.set_light_state.call_args
    assert kwargs[1]["on"] is True


@pytest.mark.anyio
async def test_execute_light_action_off():
    ha = AsyncMock()
    ha.list_entities.return_value = [
        {"entity_id": "light.desk", "attributes": {"friendly_name": "Desk Light", "area_id": "office"}},
    ]
    result = await _execute_light_action("ha.light_off", {"name": "desk"}, ha)
    assert result is True
    ha.set_light_state.assert_called_once()
    kwargs = ha.set_light_state.call_args
    assert kwargs[1]["on"] is False


@pytest.mark.anyio
async def test_execute_light_action_no_targets():
    ha = AsyncMock()
    ha.list_entities.return_value = []
    result = await _execute_light_action("ha.light_on", {"name": "nonexistent"}, ha)
    assert result is False


# ===== _maybe_execute_scheduler_action =====


@pytest.mark.anyio
async def test_scheduler_reminder_create():
    sched = AsyncMock()
    svc = AsyncMock()
    result = await _maybe_execute_scheduler_action(
        "reminder.create", "message=Take medicine, when=tomorrow at 9am", sched, svc
    )
    assert result is True
    svc.create_reminder.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_reminder_create_no_message():
    svc = AsyncMock()
    result = await _maybe_execute_scheduler_action("reminder.create", "", None, svc)
    # arg_string becomes the message, but if _parse_action_args yields empty message key,
    # the arg_string itself is used. Empty string is falsy -> returns False
    # Actually "" is passed as arg_string and as fallback message. "" is falsy -> False
    assert result is False


@pytest.mark.anyio
async def test_scheduler_reminder_create_fallback_to_scheduler():
    sched = AsyncMock()
    result = await _maybe_execute_scheduler_action("reminder.create", "message=Do thing, when=tomorrow", sched, None)
    assert result is True
    sched.schedule_reminder.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_timer_start():
    svc = AsyncMock()
    with mock_patch("pulse.assistant.actions.parse_duration_seconds", return_value=300):
        result = await _maybe_execute_scheduler_action("timer.start", "duration=5m, label=cooking", None, svc)
    assert result is True
    svc.create_timer.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_timer_start_zero_duration():
    svc = AsyncMock()
    with mock_patch("pulse.assistant.actions.parse_duration_seconds", return_value=0):
        result = await _maybe_execute_scheduler_action("timer.start", "duration=0", None, svc)
    assert result is False


@pytest.mark.anyio
async def test_scheduler_timer_extend():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "t1", "label": "cooking"}])
    with mock_patch("pulse.assistant.actions.parse_duration_seconds", return_value=60):
        result = await _maybe_execute_scheduler_action("timer.add", "duration=1m, label=cooking", None, svc)
    assert result is True
    svc.extend_timer.assert_called_once_with("t1", 60)


@pytest.mark.anyio
async def test_scheduler_timer_stop():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "t1", "label": "cooking"}])
    result = await _maybe_execute_scheduler_action("timer.stop", "label=cooking", None, svc)
    assert result is True
    svc.stop_event.assert_called_once_with("t1", reason="action_stop")


@pytest.mark.anyio
async def test_scheduler_timer_cancel_all():
    svc = AsyncMock()
    result = await _maybe_execute_scheduler_action("timer.cancel_all", "", None, svc)
    assert result is True
    svc.cancel_all_timers.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_alarm_set():
    svc = AsyncMock()
    with mock_patch("pulse.assistant.actions.parse_day_tokens", return_value=None):
        result = await _maybe_execute_scheduler_action("alarm.set", "time=7:00am, label=morning", None, svc)
    assert result is True
    svc.create_alarm.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_alarm_set_empty_time():
    svc = AsyncMock()
    result = await _maybe_execute_scheduler_action("alarm.set", "", None, svc)
    # arg_string is empty, so time_text becomes empty string, which is falsy -> False
    assert result is False


@pytest.mark.anyio
async def test_scheduler_alarm_update():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "a1", "label": "morning"}])
    result = await _maybe_execute_scheduler_action("alarm.update", "label=morning, time=7:30am", None, svc)
    assert result is True
    svc.update_alarm.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_alarm_delete():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "a1", "label": "morning"}])
    result = await _maybe_execute_scheduler_action("alarm.delete", "label=morning", None, svc)
    assert result is True
    svc.delete_event.assert_called_once_with("a1")


@pytest.mark.anyio
async def test_scheduler_alarm_stop():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "a1", "label": "wake"}])
    result = await _maybe_execute_scheduler_action("alarm.stop", "label=wake", None, svc)
    assert result is True
    svc.stop_event.assert_called_once_with("a1", reason="action_stop")


@pytest.mark.anyio
async def test_scheduler_alarm_snooze():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "a1", "label": "wake"}])
    result = await _maybe_execute_scheduler_action("alarm.snooze", "label=wake, minutes=10", None, svc)
    assert result is True
    svc.snooze_alarm.assert_called_once_with("a1", minutes=10)


@pytest.mark.anyio
async def test_scheduler_alarm_snooze_default_minutes():
    svc = AsyncMock()
    svc.list_events = Mock(return_value=[{"id": "a1", "label": "wake"}])
    result = await _maybe_execute_scheduler_action("alarm.snooze", "label=wake", None, svc)
    assert result is True
    svc.snooze_alarm.assert_called_once_with("a1", minutes=5)


@pytest.mark.anyio
async def test_scheduler_timer_start_fallback_to_scheduler():
    sched = AsyncMock()
    with mock_patch("pulse.assistant.actions.parse_duration_seconds", return_value=120):
        result = await _maybe_execute_scheduler_action("timer.start", "duration=2m, label=eggs", sched, None)
    assert result is True
    sched.start_timer.assert_called_once()


@pytest.mark.anyio
async def test_scheduler_unknown_slug():
    result = await _maybe_execute_scheduler_action("unknown.action", "", None, None)
    assert result is False


# ===== _maybe_execute_media_action =====


@pytest.mark.anyio
async def test_media_pause():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.pause", "", mc)
    assert result is True
    mc.pause_all.assert_called_once()


@pytest.mark.anyio
async def test_media_resume():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.resume", "", mc)
    assert result is True
    mc.resume_all.assert_called_once()


@pytest.mark.anyio
async def test_media_stop():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.stop", "", mc)
    assert result is True
    mc.stop_all.assert_called_once()


@pytest.mark.anyio
async def test_media_mute():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.mute", "", mc)
    assert result is True
    mc.pause_all.assert_called_once()


@pytest.mark.anyio
async def test_media_play():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.play", "", mc)
    assert result is True
    mc.resume_all.assert_called_once()


@pytest.mark.anyio
async def test_media_halt():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.halt", "", mc)
    assert result is True
    mc.stop_all.assert_called_once()


@pytest.mark.anyio
async def test_media_volume_set():
    mc = AsyncMock()
    with mock_patch("pulse.assistant.actions.set_volume") as mock_vol:
        result = await _maybe_execute_media_action("volume.set", "percentage=50", mc)
    assert result is True
    mock_vol.assert_called_once_with(50, None)


@pytest.mark.anyio
async def test_media_volume_set_with_volume_key():
    mc = AsyncMock()
    with mock_patch("pulse.assistant.actions.set_volume") as mock_vol:
        result = await _maybe_execute_media_action("volume.set", "volume=75", mc)
    assert result is True
    mock_vol.assert_called_once_with(75, None)


@pytest.mark.anyio
async def test_media_volume_set_no_value():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("volume.set", "", mc)
    assert result is False


@pytest.mark.anyio
async def test_media_none_controller():
    result = await _maybe_execute_media_action("media.pause", "", None)
    assert result is False


@pytest.mark.anyio
async def test_media_unknown_slug():
    mc = AsyncMock()
    result = await _maybe_execute_media_action("media.skip", "", mc)
    assert result is False


# ===== _duration_from_args =====


def test_duration_from_args_with_duration_key():
    with mock_patch("pulse.assistant.actions.parse_duration_seconds", return_value=120.0):
        result = _duration_from_args({"duration": "2m"}, "")
    assert result == 120.0


def test_duration_from_args_fallback():
    with mock_patch("pulse.assistant.actions.parse_duration_seconds", return_value=60.0):
        result = _duration_from_args({}, "1m")
    assert result == 60.0


def test_duration_from_args_empty():
    result = _duration_from_args({}, "")
    assert result == 0.0


# ===== _playback_from_args =====


def test_playback_from_args_beep_default():
    result = _playback_from_args({})
    assert result.mode == "beep"


def test_playback_from_args_music():
    result = _playback_from_args({"type": "music", "source": "spotify:playlist:123", "entity": "media_player.speaker"})
    assert result.mode == "music"
    assert result.music_source == "spotify:playlist:123"
    assert result.music_entity == "media_player.speaker"


def test_playback_from_args_non_music_mode():
    result = _playback_from_args({"type": "alarm"})
    assert result.mode == "beep"


# ===== _resolve_schedule_event_id =====


def test_resolve_schedule_event_id_by_id():
    svc = Mock()
    result = _resolve_schedule_event_id(svc, "timer", {"id": "t123"})
    assert result == "t123"


def test_resolve_schedule_event_id_by_event_id():
    svc = Mock()
    result = _resolve_schedule_event_id(svc, "timer", {"event_id": "t456"})
    assert result == "t456"


def test_resolve_schedule_event_id_by_label():
    svc = Mock()
    svc.list_events.return_value = [
        {"id": "t1", "label": "Cooking Timer"},
        {"id": "t2", "label": "Laundry Timer"},
    ]
    result = _resolve_schedule_event_id(svc, "timer", {"label": "cooking"})
    assert result == "t1"


def test_resolve_schedule_event_id_no_match():
    svc = Mock()
    svc.list_events.return_value = [{"id": "t1", "label": "Cooking"}]
    result = _resolve_schedule_event_id(svc, "timer", {"label": "nonexistent"})
    assert result is None


def test_resolve_schedule_event_id_no_label():
    svc = Mock()
    result = _resolve_schedule_event_id(svc, "timer", {})
    assert result is None


# ===== _reminder_repeat_from_args =====


def test_reminder_repeat_empty():
    dt = datetime(2025, 6, 15, 9, 0)
    assert _reminder_repeat_from_args({}, dt) is None


def test_reminder_repeat_none_string():
    dt = datetime(2025, 6, 15, 9, 0)
    assert _reminder_repeat_from_args({"repeat": "none"}, dt) is None


def test_reminder_repeat_daily():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "daily"}, dt)
    assert result == {"type": "weekly", "days": list(range(7))}


def test_reminder_repeat_weekdays():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "weekdays"}, dt)
    assert result == {"type": "weekly", "days": [0, 1, 2, 3, 4]}


def test_reminder_repeat_weekly():
    dt = datetime(2025, 6, 15, 9, 0)
    with mock_patch("pulse.assistant.actions.parse_day_tokens", return_value=None):
        result = _reminder_repeat_from_args({"repeat": "weekly"}, dt)
    assert result == {"type": "weekly", "days": list(range(7))}


def test_reminder_repeat_weekly_with_days():
    dt = datetime(2025, 6, 15, 9, 0)
    with mock_patch("pulse.assistant.actions.parse_day_tokens", return_value=[0, 2, 4]):
        result = _reminder_repeat_from_args({"repeat": "weekly", "days": "mon,wed,fri"}, dt)
    assert result == {"type": "weekly", "days": [0, 2, 4]}


def test_reminder_repeat_monthly():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "monthly"}, dt)
    assert result == {"type": "monthly", "day": 15}


def test_reminder_repeat_monthly_with_day():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "monthly", "day": "1"}, dt)
    assert result == {"type": "monthly", "day": 1}


def test_reminder_repeat_monthly_invalid_day():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "monthly", "day": "abc"}, dt)
    assert result == {"type": "monthly", "day": 15}


def test_reminder_repeat_interval_days():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"interval_days": "3"}, dt)
    assert result == {"type": "interval", "interval_days": 3}


def test_reminder_repeat_interval_days_invalid():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"interval_days": "bad"}, dt)
    assert result is None


def test_reminder_repeat_interval_months():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"interval_months": "2"}, dt)
    assert result == {"type": "interval", "interval_months": 2}


def test_reminder_repeat_interval_months_invalid():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"interval_months": "bad"}, dt)
    assert result is None


def test_reminder_repeat_numeric_months_pattern():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "3 months"}, dt)
    assert result == {"type": "interval", "interval_months": 3}


def test_reminder_repeat_numeric_weeks_pattern():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "2 weeks"}, dt)
    assert result == {"type": "interval", "interval_days": 14}


def test_reminder_repeat_numeric_days_pattern():
    dt = datetime(2025, 6, 15, 9, 0)
    result = _reminder_repeat_from_args({"repeat": "5 days"}, dt)
    assert result == {"type": "interval", "interval_days": 5}
