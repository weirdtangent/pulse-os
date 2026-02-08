# Pulse Assistant Refactoring Plan

## Overview

This document outlines a comprehensive plan to refactor `bin/pulse-assistant.py` (2,893 lines) into modular, maintainable components. The goal is to reduce the main entry point to ~250 lines while extracting reusable business logic into dedicated modules.

## Current State

- **Main file:** `bin/pulse-assistant.py` - 2,893 lines
- **Main class:** `PulseAssistant` - 141 methods
- **Test coverage:** 10% overall (1,116 test lines / 10,995 source lines)

## Target State

- **Entry point:** `bin/pulse-assistant.py` - ~250 lines (orchestration only)
- **Extracted modules:** 11 new modules under `pulse/assistant/`
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

**Benefits:**
- Clear separation of concerns
- Easier to test MQTT interactions
- Reduces coupling in main class

### Phase 2: Extract Preference Manager ✅
**File:** `pulse/assistant/preference_manager.py` (~300 lines)
**Status:** Completed

Manage user preferences and sound settings.

**Extracted:**
- `PreferenceManager` class
- Sound option management
- Preference MQTT command handlers
- Sound library integration

**Benefits:**
- Isolates preference logic
- Makes sound management reusable
- Clear API for preference updates

### Phase 3: Extract Schedule Intent Parser ✅
**File:** `pulse/assistant/schedule_intents.py` (~700 lines)
**Status:** Completed

Parse natural language into schedule intents.

**Extracted:**
- `ScheduleIntentParser` class
- Timer intent extraction
- Alarm intent extraction
- Reminder intent extraction
- Date/time parsing utilities
- Confirmation message formatting

**Benefits:**
- Highly testable (pure functions)
- Reusable for other voice assistants
- Clear separation from execution logic

### Phase 4: Extract Schedule Shortcuts Handler ✅
**File:** `pulse/assistant/schedule_shortcuts.py` (~400 lines)
**Status:** Completed

Handle voice shortcuts for schedules.

**Extracted:**
- `ScheduleShortcutHandler` class
- Stop/cancel operations
- Timer extend operations
- List displays (alarms, reminders, events)

**Benefits:**
- Works with ScheduleIntentParser
- Testable in isolation
- Clear command handling patterns

### Phase 5: Extract Schedule Command Processor ✅
**File:** `pulse/assistant/schedule_commands.py` (~510 lines)
**Status:** Completed

Process MQTT schedule commands.

**Extracted:**
- `ScheduleCommandProcessor` class
- MQTT command message handling
- Payload parsing and validation
- Schedule state change callbacks

**Benefits:**
- Completes schedule-related extraction
- Clear MQTT → ScheduleService bridge
- Easier to add new schedule commands

### Phase 6: Extract Calendar Manager
**File:** `pulse/assistant/calendar_manager.py` (~150 lines)

Manage calendar event state and reminders.

**Extract:**
- `CalendarEventManager` class
- Calendar reminder triggering
- Calendar snapshot handling
- Event deduplication and filtering

**Benefits:**
- Small, focused module
- Clear calendar integration point
- Easier to test calendar logic

### Phase 7: Extract Music Handler
**File:** `pulse/assistant/music_handler.py` (~150 lines)

Handle music-related voice commands.

**Extract:**
- `MusicCommandHandler` class
- Music command detection
- Media service calls
- Track description formatting

**Benefits:**
- Self-contained feature
- Easy to extend with new music commands
- Clear Home Assistant integration

### Phase 8: Extract Information Query Handler
**File:** `pulse/assistant/info_query_handler.py` (~100 lines)

Wrapper for info service integration.

**Extract:**
- `InfoQueryHandler` class
- Info query detection
- Speech duration estimation
- Info overlay coordination

**Benefits:**
- Completes command handler extraction
- Integrates existing InfoService
- Clear information flow

### Phase 9: Extract Earmuffs Manager
**File:** `pulse/assistant/earmuffs.py` (~100 lines)

Manage wake word suppression.

**Extract:**
- `EarmuffsManager` class
- Earmuffs state management
- MQTT command handling
- Thread-safe enable/disable

**Benefits:**
- Focused feature module
- Clear wake word integration
- Easier to add automation rules

### Phase 10: Extract Pipeline Orchestrator
**File:** `pulse/assistant/pipeline_orchestrator.py` (~400 lines)

Orchestrate Pulse and Home Assistant pipelines.

**Extract:**
- `PipelineOrchestrator` class
- Pulse pipeline flow (wake → STT → LLM → TTS)
- Home Assistant pipeline integration
- Audio processing (transcribe, speak)
- Stop phrase detection
- LLM turn execution

**Benefits:**
- Core business logic extracted
- All command handlers integrated here
- Clear pipeline flows
- Highly testable with mocks

### Phase 11: Extract Alert/Intercom Handler
**File:** `pulse/assistant/alerts.py` (~100 lines)

Handle alert and intercom messages.

**Extract:**
- `AlertHandler` class
- Alert message handling
- Intercom message handling
- Now playing message handling

**Benefits:**
- Small, focused feature
- Clear MQTT integration
- Easy to extend alert types

### Phase 12: Refactor Main Entry Point
**File:** `bin/pulse-assistant.py` (~250 lines)

Reduce to minimal orchestration.

**Remains:**
- `PulseAssistant` class (coordinator)
- Component initialization
- Main run loop (delegating to orchestrator)
- Shutdown handling
- Entry point (`main()`)

**Benefits:**
- Clear system architecture
- Easy to understand flow
- Minimal coupling
- All logic in tested modules

## Module Dependency Graph

```
bin/pulse-assistant.py (entry point)
├── PipelineOrchestrator
│   ├── ScheduleShortcutHandler
│   │   └── ScheduleIntentParser
│   ├── MusicCommandHandler
│   ├── InfoQueryHandler
│   ├── ConversationManager (existing)
│   ├── ActionEngine (existing)
│   └── RoutineEngine (existing)
├── AssistantMqttPublisher
├── PreferenceManager
├── ScheduleCommandProcessor
├── CalendarEventManager
├── EarmuffsManager
├── AlertHandler
├── ScheduleService (existing)
├── CalendarSyncService (existing)
├── WakeDetector (existing)
├── MediaController (existing)
└── HomeAssistantClient (existing)
```

## Implementation Strategy

### Incremental Approach

1. Extract one phase at a time
2. Run tests after each extraction
3. Manually verify affected features
4. Commit each phase separately

### Testing Strategy

After each phase:
- Run existing test suite: `pytest tests/`
- Manual smoke tests of affected features
- Verify MQTT message formats unchanged
- Check logs for missing functionality

### Dependency Management

**Principle:** Extract from leaf to root
- Start with low-dependency modules (MQTT, Preferences)
- Move to mid-level modules (Intent parsers, Handlers)
- End with orchestration (depends on everything)

**Pattern:**
- Pass dependencies through constructors
- Use callback functions for cross-communication
- Maintain single direction of dependencies

### Breaking Circular Dependencies

If circular dependencies arise:
- Use dependency injection (pass objects to constructors)
- Use callback functions instead of direct calls
- Extract shared interfaces/protocols
- Move shared code to separate utility modules

## File Size Estimates (After Refactoring)

| Module | Lines | Purpose |
|--------|-------|---------|
| `bin/pulse-assistant.py` | ~250 | Entry point, coordinator |
| `mqtt_publisher.py` | ~500 | MQTT publishing, state management |
| `preference_manager.py` | ~300 | User preferences, sounds |
| `schedule_intents.py` | ~700 | NLP → schedule intents |
| `schedule_shortcuts.py` | ~400 | Voice schedule shortcuts |
| `schedule_commands.py` | ~300 | MQTT schedule commands |
| `calendar_manager.py` | ~150 | Calendar integration |
| `music_handler.py` | ~150 | Music commands |
| `info_query_handler.py` | ~100 | Information queries |
| `earmuffs.py` | ~100 | Wake word suppression |
| `alerts.py` | ~100 | Alerts and intercom |
| `pipeline_orchestrator.py` | ~400 | Pipeline flows |

## Testing Plan

### Unit Tests to Add

For each extracted module:
- Test all public methods
- Mock external dependencies (MQTT, Home Assistant, etc.)
- Test error handling paths
- Test edge cases

### Integration Tests to Add

- Pipeline flow tests (wake → response)
- Schedule creation/cancellation flows
- Preference updates propagate correctly
- Calendar reminder triggering

### Target Coverage

- Current: 10%
- Target: 60%+
- Priority modules:
  1. ScheduleIntentParser (pure functions, easy to test)
  2. PipelineOrchestrator (core business logic)
  3. PreferenceManager (state management)
  4. ScheduleCommandProcessor (MQTT integration)

## Benefits Summary

### Maintainability
- Smaller, focused modules
- Clear responsibilities
- Easier code navigation
- Better IDE support

### Testability
- Isolated units to test
- Easier mocking
- Higher coverage achievable
- Faster test execution

### Reusability
- Modules usable in other projects
- Clear APIs
- Minimal coupling
- Good candidates for package extraction

### Onboarding
- New contributors understand structure faster
- Clear where to add features
- Obvious where to find code
- Better documentation opportunities

## Risks and Mitigations

### Risk: Breaking existing functionality
**Mitigation:**
- Incremental extraction
- Test after each phase
- Manual verification
- Git branches for each phase

### Risk: Circular dependencies
**Mitigation:**
- Follow leaf-to-root extraction order
- Use dependency injection
- Extract shared code to utilities

### Risk: Performance regression
**Mitigation:**
- Profile before and after
- Monitor MQTT message rates
- Check pipeline latency
- Test on actual hardware

### Risk: Testing overhead
**Mitigation:**
- Use pytest fixtures for common setups
- Create mock factories
- Share test utilities
- Parallel test execution

## Next Steps

1. **Review and approve** this plan
2. **Set up branch:** `git checkout -b refactor/extract-modules`
3. **Start with Phase 1:** Extract MQTT Publisher
4. **Iterate** through phases 2-12
5. **Add tests** alongside each extraction
6. **Final review** and merge

## References

- [location_resolver.py:19-26](../pulse/location_resolver.py#L19-L26) - Example of clean dataclass
- [wake_detector.py](../pulse/assistant/wake_detector.py) - Example extracted module
- [media_controller.py](../pulse/assistant/media_controller.py) - Example extracted module
- [conversation_manager.py](../pulse/assistant/conversation_manager.py) - Example extracted module

## Timeline Estimate

- **Phase 1-2:** 1-2 days (MQTT + Preferences)
- **Phase 3-5:** 2-3 days (Schedule extraction)
- **Phase 6-9:** 1-2 days (Smaller features)
- **Phase 10-11:** 2-3 days (Orchestrator + cleanup)
- **Phase 12:** 1 day (Main file refactor)
- **Total:** 7-11 days of development + testing

This is a living document and should be updated as implementation progresses.
