# Pulse Assistant Refactoring Plan

## Overview

This document outlines a comprehensive plan to refactor `bin/pulse-assistant.py` into modular, maintainable components. The goal is to reduce the main entry point to ~500 lines while extracting reusable business logic into dedicated modules.

## Current State

- **Main file:** `bin/pulse-assistant.py` - 496 lines (down from 2,893)
- **Test suite:** 800 tests across 33 test files (56% coverage)
- **Phases 1–14:** Completed
- **Cleanup:** Completed

## Target State

- **Entry point:** `bin/pulse-assistant.py` - ~500 lines (coordinator + schedule/calendar callbacks)
- **Extracted modules:** 13 modules under `pulse/assistant/`
- **Each module:** 100-700 lines, single responsibility
- **Improved testability:** Each module independently testable

## Refactoring Phases

### Phase 1: Extract MQTT Publisher ✅
**File:** `pulse/assistant/mqtt_publisher.py` (~500 lines)
**Status:** Completed

Centralize all MQTT publishing logic and state management.

**Extracted:**
- `AssistantMqttPublisher` class
- All `_publish_*()` methods
- Home Assistant discovery methods
- State tracking for assist stages, pipelines, etc.

### Phase 2: Extract Preference Manager ✅
**File:** `pulse/assistant/preference_manager.py` (~300 lines)
**Status:** Completed

Manage user preferences and sound settings.

**Extracted:**
- `PreferenceManager` class
- Sound option management
- Preference MQTT command handlers
- Sound library integration

### Phase 3: Extract Schedule Intent Parser ✅
**File:** `pulse/assistant/schedule_intents.py` (~700 lines)
**Status:** Completed

Parse natural language into schedule intents.

**Extracted:**
- `ScheduleIntentParser` class
- Timer/alarm/reminder intent extraction
- Date/time parsing utilities
- Confirmation message formatting

### Phase 4: Extract Schedule Shortcuts Handler ✅
**File:** `pulse/assistant/schedule_shortcuts.py` (~400 lines)
**Status:** Completed

Handle voice shortcuts for schedules.

**Extracted:**
- `ScheduleShortcutHandler` class
- Stop/cancel operations
- Timer extend operations
- List displays (alarms, reminders, events)

### Phase 5: Extract Schedule Command Processor ✅
**File:** `pulse/assistant/schedule_commands.py` (~510 lines)
**Status:** Completed

Process MQTT schedule commands.

**Extracted:**
- `ScheduleCommandProcessor` class
- MQTT command message handling
- Payload parsing and validation
- Schedule state change callbacks

### Cleanup: Remove Dead Code ✅
**Status:** Completed

Removed backward-compatibility wrappers and no-ops left over from earlier extractions.

### Phase 6: Extract Calendar Manager ✅
**File:** `pulse/assistant/calendar_manager.py` (~200 lines)
**Status:** Completed

Manage calendar event state and reminders.

**Extracted:**
- `CalendarEventManager` class
- Calendar reminder triggering
- Calendar snapshot handling
- Event deduplication and filtering
- Event serialization

### Phase 7: Extract Music & Info Query Handlers ✅
**File:** `pulse/assistant/music_handler.py` (~156 lines)
**File:** `pulse/assistant/info_query_handler.py` (~134 lines)
**Status:** Completed

Handle music voice commands and information query integration.

**Extracted:**
- `MusicCommandHandler` class — music command detection, media service calls, track description formatting
- `InfoQueryHandler` class — info query detection, speech duration estimation, info overlay coordination

### Phase 8: Extract Earmuffs Manager ✅
**File:** `pulse/assistant/earmuffs.py` (~128 lines)
**Status:** Completed

Manage wake word suppression.

**Extracted:**
- `EarmuffsManager` class
- Earmuffs state management
- MQTT command handling
- Thread-safe enable/disable

### Phase 9: Extract Event Handlers ✅
**File:** `pulse/assistant/event_handlers.py` (~172 lines)
**Status:** Completed

Handle alert, intercom, now-playing, and kiosk availability events.

**Extracted:**
- `EventHandlerManager` class
- Alert message handling (JSON + plain text parsing)
- Intercom message handling
- Now playing / playback telemetry handling
- Kiosk availability tracking and auto-restart
- MQTT topic subscriptions

### Phase 10: Extract Pipeline Orchestrator ✅
**File:** `pulse/assistant/pipeline_orchestrator.py` (~686 lines)
**Status:** Completed

Orchestrate Pulse and Home Assistant pipelines.

**Extracted:**
- `PipelineOrchestrator` class
- `AssistRunTracker` dataclass (stage/metrics tracking)
- Pulse pipeline flow (wake → STT → LLM → TTS)
- Home Assistant pipeline integration (Assist API + TTS audio)
- Follow-up conversation loop
- Audio processing (transcribe, speak, TTS, PCM playback)
- Stop phrase detection
- LLM turn execution with action/routine dispatch
- HA response extraction helpers (static methods)
- Home Assistant prompt action definitions
- Assist stage and metrics publishing

### Phase 11: Refactor Main Entry Point ✅
**File:** `bin/pulse-assistant.py` (~496 lines)
**Status:** Effectively completed via Phase 9-10 extractions

The main file now contains only:
- `PulseAssistant` class (coordinator)
- Component initialization and wiring
- Main run loop (delegating to orchestrator)
- Heartbeat loop (delegating kiosk check to event handlers)
- Shutdown handling
- Schedule/calendar callbacks
- Activity logging
- LLM provider builder
- Entry point (`main()`)

## Module Dependency Graph

```
bin/pulse-assistant.py (entry point, coordinator)
├── PipelineOrchestrator
│   ├── ScheduleShortcutHandler
│   │   └── ScheduleIntentParser
│   ├── MusicCommandHandler
│   ├── InfoQueryHandler
│   ├── ConversationManager (existing)
│   ├── ActionEngine (existing)
│   └── RoutineEngine (existing)
├── EventHandlerManager
├── AssistantMqttPublisher
├── PreferenceManager
├── ScheduleCommandProcessor
├── CalendarEventManager
├── EarmuffsManager
├── ScheduleService (existing)
├── CalendarSyncService (existing)
├── WakeDetector (existing)
├── MediaController (existing)
└── HomeAssistantClient (existing)
```

## File Size Summary (Actual)

| Module | Lines | Purpose |
|--------|-------|---------|
| `bin/pulse-assistant.py` | 483 | Entry point, coordinator |
| `pipeline_orchestrator.py` | 686 | Pipeline flows, audio, stage tracking |
| `mqtt_publisher.py` | ~500 | MQTT publishing, state management |
| `preference_manager.py` | ~300 | User preferences, sounds |
| `schedule_intents.py` | ~700 | NLP → schedule intents |
| `schedule_shortcuts.py` | ~400 | Voice schedule shortcuts |
| `schedule_commands.py` | ~300 | MQTT schedule commands |
| `calendar_manager.py` | ~200 | Calendar integration |
| `event_handlers.py` | 172 | Alerts, intercom, now-playing, kiosk |
| `music_handler.py` | 156 | Music commands |
| `info_query_handler.py` | 134 | Information queries |
| `earmuffs.py` | 128 | Wake word suppression |

## Remaining Work

The core extraction refactoring is complete. Remaining work focuses on a final
small extraction and increasing test coverage.

### Phase 12: Move LLM Builder to `llm.py` ✅
**Status:** Completed

Move `_build_llm_provider` and `_rebuild_llm_provider` logic into
`pulse/assistant/llm.py` as `build_llm_provider_with_overrides()`. The main
file call site becomes a thin two-line delegation. Natural home since `llm.py`
already contains all provider classes and `build_llm_provider()`.

Main file reduced from 496 → 483 lines.

### Phase 13: Configure pytest-cov ✅
**Status:** Completed

Added `--cov=pulse --cov-report=term-missing:skip-covered` to pytest addopts
and `pytest-cov` to the dev dependency group in `pyproject.toml`.

### Phase 14: Test Easy Untested Modules ✅
**Status:** Completed

Added 96 tests across 4 new test files:
- `test_conversation_manager.py` — 39 tests (97% coverage)
- `test_routines.py` — 20 tests (46% → covered)
- `test_scheduler.py` — 16 tests (36% → covered)
- `test_media_controller.py` — 21 tests (21% → 84% coverage)

Total suite: 800 tests, 56% overall coverage (up from 54%).

### Phase 15: Test Medium Modules
Add tests for larger modules with external dependencies:
- `config.py` (771 lines) — dataclass validation, env parsing
- `info_service.py` (476 lines) — info query orchestration
- `wyoming.py` (229 lines) — Wyoming protocol client

### Dropped: Extract Schedule Callbacks
The 5 schedule callback methods in `bin/pulse-assistant.py` are thin delegation
wrappers (2-5 lines each) that wire components together — exactly the
coordinator's responsibility. Not worth extracting into a separate module.

## References

- [wake_detector.py](../pulse/assistant/wake_detector.py) - Example extracted module
- [media_controller.py](../pulse/assistant/media_controller.py) - Example extracted module
- [conversation_manager.py](../pulse/assistant/conversation_manager.py) - Example extracted module
- [earmuffs.py](../pulse/assistant/earmuffs.py) - Callback pattern example
- [music_handler.py](../pulse/assistant/music_handler.py) - Handler pattern example
