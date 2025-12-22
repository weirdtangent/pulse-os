from pulse.assistant.response_modes import select_ha_response


def test_select_response_non_ha_actions_passthrough() -> None:
    text, tone = select_ha_response("full", ["routine.play"], "Hello")
    assert text == "Hello"
    assert tone is False


def test_select_response_none_mode() -> None:
    text, tone = select_ha_response("none", ["ha.turn_on:light"], "Hello")
    assert text is None
    assert tone is False


def test_select_response_tone_mode() -> None:
    text, tone = select_ha_response("tone", ["ha.turn_off:light"], "Hello")
    assert text is None
    assert tone is True


def test_select_response_minimal_mode() -> None:
    text, tone = select_ha_response("minimal", ["ha.light_on"], "Detailed response")
    assert text == "Ok."
    assert tone is False


def test_config_defaults_include_ha_response_and_tone() -> None:
    from pulse.assistant.config import AssistantConfig

    env = {
        "PULSE_ASSISTANT_HA_RESPONSE_MODE": "",
        "PULSE_ASSISTANT_HA_TONE_SOUND": "",
    }
    config = AssistantConfig.from_env(env)
    assert config.preferences.ha_response_mode == "full"
    assert config.preferences.ha_tone_sound == "alarm-sonar"


def test_config_respects_env_for_ha_response_and_tone() -> None:
    from pulse.assistant.config import AssistantConfig

    env = {
        "PULSE_ASSISTANT_HA_RESPONSE_MODE": "tone",
        "PULSE_ASSISTANT_HA_TONE_SOUND": "custom-tone",
        "PULSE_ASSISTANT_WAKE_WORDS_PULSE": "hey_jarvis",
        "PULSE_ASSISTANT_WAKE_WORDS_HA": "ok_nabu",
    }
    config = AssistantConfig.from_env(env)
    assert config.preferences.ha_response_mode == "tone"
    assert config.preferences.ha_tone_sound == "custom-tone"
