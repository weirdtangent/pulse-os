# Test Suite Results

## Summary

**Status:** All tests passing
**Total tests:** 1834 across 35 test files
**Coverage:** 77%
**Target:** 75% for v1.0 ✅

## Test Suites

| Test File | Tests | Module Under Test |
|-----------|------:|-------------------|
| test_schedule_service.py | 164 | Schedule service orchestration |
| test_actions.py | 159 | Action parsing and execution engine |
| test_schedule_intents.py | 121 | Schedule intent NLP parsing |
| test_llm.py | 105 | LLM providers and response parsing |
| test_info_service.py | 102 | Info query orchestration |
| test_info_sources.py | 99 | Weather/news/sports APIs |
| test_home_assistant.py | 87 | Home Assistant client API |
| test_config.py | 86 | Dataclass config, env parsing |
| test_calendar_sync.py | 81 | iCal/WebCal sync + RRULE expansion |
| test_pipeline_orchestrator.py | 75 | Voice pipeline flows (wake/STT/LLM/TTS) |
| test_schedule_shortcuts.py | 63 | Voice schedule shortcuts |
| test_schedule_commands.py | 62 | MQTT schedule command processing |
| test_preference_manager.py | 59 | User preferences and sounds |
| test_overlay.py | 56 | Overlay rendering and layout |
| test_datetime_utils.py | 51 | Datetime parsing and timezone handling |
| test_wake_detector.py | 49 | Wake word detection |
| test_music_handler.py | 44 | Music voice commands |
| test_conversation_manager.py | 39 | Multi-turn conversation state |
| test_earmuffs.py | 37 | Wake word suppression |
| test_wyoming.py | 35 | Wyoming protocol STT/TTS/wake |
| test_media_controller.py | 33 | Media player control |
| test_config_persist.py | 30 | Config file persistence |
| test_mqtt_publisher.py | 29 | MQTT publishing and HA discovery |
| test_mqtt_client.py | 29 | MQTT client lifecycle |
| test_calendar_manager.py | 29 | Calendar event management |
| test_event_handlers.py | 26 | Alert, intercom, now-playing, kiosk events |
| test_routines.py | 19 | Scene automation routines |
| test_scheduler.py | 16 | Timer/reminder scheduling |
| test_info_query_handler.py | 16 | Info query detection and dispatch |
| test_utils.py | 8 | Utility functions |
| test_assistant_shortcuts.py | 7 | Stop phrases, time-of-day extraction |
| test_systemd_notify.py | 6 | Systemd notification |
| test_response_modes.py | 6 | Response mode selection |
| test_schedule_service_pauses.py | 4 | Schedule service pause/resume |
| test_sound_library.py | 2 | Sound file loading |

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_home_assistant.py -v

# Run a single test
uv run pytest tests/test_llm.py::test_openai_provider -v

# Run with coverage report
uv run pytest tests/ --cov=pulse --cov-report=html
open htmlcov/index.html
```

## Coverage Progress

| Milestone | Tests | Coverage | Date |
|-----------|------:|----------|------|
| Initial test suite | 86 | ~15% | Dec 2025 |
| Post-refactoring (Phase 10) | 700 | ~50% | Jan 2026 |
| Phase 14 | 800 | 56% | Feb 2026 |
| Phase 15 | >1000 | 58% | Mar 2026 |
| Coverage push | 1834 | 77% | Apr 2026 |
| v1.0 target | — | 75% | Apr 2026 ✅ |

## Remaining Coverage Gaps

Modules that would benefit from more tests:
- `bin/kiosk-mqtt-listener.py` — kiosk overlay and MQTT listener
- `pulse/overlay_server.py` — overlay HTTP server (0%)
- `pulse/display.py` — display hardware control (0%)
- `pulse/audio.py` — low-level audio playback (23%)
