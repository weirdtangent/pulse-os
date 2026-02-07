# Phase 1: MQTT Publisher Extraction - Detailed Plan

## Executive Summary

Extract MQTT publishing logic from `bin/pulse-assistant.py` (3,197 lines) into a new `pulse/assistant/mqtt_publisher.py` module (~500 lines). This phase focuses on **publish-only logic** while keeping subscription/handler logic in the main class.

## Current State Analysis

- **Total _publish methods:** 9 core methods
- **Supporting helpers:** 14 methods
- **State variables:** 35 variables (23 topics + 12 state tracking)
- **Call sites:** 50+ locations throughout pulse-assistant.py
- **Dependencies:** AssistantMqtt, AssistantConfig, HomeAssistantClient, ScheduleService, SoundLibrary

## Extraction Strategy

### Chosen Approach: Composition with Stateless Publisher

Create `AssistantMqttPublisher` class that PulseAssistant holds as `self.publisher`.

**Key Decision: Publisher is MOSTLY STATELESS**
- State variables remain in PulseAssistant (for now)
- Publisher methods receive state as parameters
- Only publisher-specific caches stay in publisher:
  - `_sound_options` (performance cache)
  - `_info_overlay_clear_task` (async task management)
  - Topic strings (part of publishing concern)

**Advantages:**
- Clear separation of concerns
- Easy to test in isolation (less mocking needed)
- Minimal changes to main class handlers
- State stays where it's used
- Can mock publisher for testing main class
- Lower risk - fewer moving parts
- Future phases can move state to proper managers

**Implementation:**
```python
# Instead of:
self.publisher._publish_preferences()  # reads internal state

# Do:
self.publisher.publish_preferences(
    preferences=self.preferences,
    log_llm=self._log_llm_messages,
    active_pipeline=self._active_ha_pipeline(),
    active_provider=self._active_llm_provider(),
    sound_settings=self._get_all_sound_settings(),
)
```

This makes the publisher a true "service" rather than a state manager.

## Detailed Extraction Plan

### Step 1: Create New Module Structure

**File:** `pulse/assistant/mqtt_publisher.py`

```python
"""MQTT publishing functionality for Pulse Assistant.

This module handles all MQTT message publishing including:
- State updates (assistant stage, pipeline, wake word)
- Info overlays (alerts, lights, health, routines)
- Schedule/calendar state
- Preference states
- Home Assistant MQTT discovery
- Earmuffs control
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from pulse.assistant.config import AssistantConfig
from pulse.assistant.home_assistant import HomeAssistantClient
from pulse.assistant.mqtt import AssistantMqtt
from pulse.assistant.schedule_service import ScheduleService
from pulse.sound_library import SoundLibrary, SoundKind

LOGGER = logging.getLogger(__name__)


class AssistantMqttPublisher:
    """Manages MQTT publishing for Pulse Assistant."""

    def __init__(
        self,
        mqtt: AssistantMqtt,
        config: AssistantConfig,
        home_assistant: HomeAssistantClient | None,
        schedule_service: ScheduleService,
        sound_library: SoundLibrary,
        preferences: Any,  # PreferencesConfig
        logger: logging.Logger | None = None,
    ):
        """Initialize MQTT publisher with dependencies."""
        self.mqtt = mqtt
        self.config = config
        self.home_assistant = home_assistant
        self.schedule_service = schedule_service
        self.sound_library = sound_library
        self.preferences = preferences
        self.logger = logger or LOGGER

        # Initialize all topic variables
        base_topic = self.config.mqtt.topic_base
        self._assist_in_progress_topic = f"{base_topic}/assistant/in_progress"
        # ... all other topics

        # Initialize state tracking variables
        self._kiosk_available = True
        self._last_kiosk_online = 0.0
        # ... all other state variables

        # Initialize sound options cache
        self._sound_options: dict[str, list[tuple[str, str]]] = {}
        self._refresh_sound_options()

    # All 9 core publish methods
    # All 14 helper methods
```

### Step 2: Extract Methods (Ordered by Dependencies)

**Group 1: Independent Helpers (no dependencies)**
1. `_get_sound_label_by_id()`
2. `_get_sound_id_by_label()`
3. `_get_sound_options_for_kind()`
4. `_refresh_sound_options()`
5. `_clone_schedule_snapshot()`

**Group 2: State Accessors**
6. `_get_earmuffs_enabled()`
7. `_active_ha_pipeline()`
8. `_active_llm_provider()`
9. `_get_current_sound_id()`
10. `_get_sound_label_or_id_for_kind()`

**Group 3: Formatters**
11. `_filter_past_calendar_events()`
12. `_format_lights_card()`

**Group 4: Overlay Management**
13. `_cancel_info_overlay_clear()`
14. `_schedule_info_overlay_clear()`

**Group 5: Core Publishers (ordered by complexity)**
15. `_publish_message()` - Base method
16. `_publish_state()` - Uses _publish_message
17. `_publish_preference_state()` - Uses _publish_message
18. `_publish_earmuffs_state()` - Uses _get_earmuffs_enabled, _publish_message
19. `_publish_info_overlay()` - Uses _schedule_info_overlay_clear, _publish_message
20. `_publish_preferences()` - Uses multiple helpers, _publish_preference_state
21. `_publish_schedule_state()` - Uses _filter_past_calendar_events, _publish_message
22. `_publish_light_overlay()` - Async, uses _format_lights_card, _publish_info_overlay
23. `_publish_assistant_discovery()` - Complex, uses _get_sound_options_for_kind

### Step 3: Update PulseAssistant Class

**Changes to `__init__`:**
```python
def __init__(self, config: AssistantConfig) -> None:
    # ... existing initialization ...

    # NEW: Initialize MQTT publisher
    self.publisher = AssistantMqttPublisher(
        mqtt=self.mqtt,
        config=self.config,
        home_assistant=self.home_assistant,
        schedule_service=self.schedule_service,
        sound_library=self._sound_library,
        preferences=self.preferences,
        logger=LOGGER,
    )

    # REMOVE: All topic variable assignments (now in publisher)
    # REMOVE: All state tracking variable assignments (now in publisher)
```

**Update all call sites (50+ locations):**
- `self._publish_*()` → `self.publisher._publish_*()`
- `self._*_topic` → `self.publisher._*_topic`
- State variables → `self.publisher._*`

### Step 4: Handle State Variable Access (REVISED)

**Decision: State variables STAY in PulseAssistant**

All state variables remain in the main class:
- `self._calendar_events`
- `self._calendar_updated_at`
- `self._latest_schedule_snapshot`
- `self._kiosk_available`
- `self._last_kiosk_online`
- `self._earmuffs_enabled`
- `self._earmuffs_manual_override`
- `self._earmuffs_state_restored`
- `self._log_llm_messages`
- All helper methods that access preferences/config

**Publisher methods receive state as parameters:**
```python
# Example:
def publish_schedule_state(
    self,
    calendar_events: list[dict[str, Any]],
    calendar_updated_at: float | None,
    schedule_snapshot: dict[str, Any] | None,
) -> None:
    """Publish current schedule and calendar state."""
    # Implementation uses passed parameters, not internal state
```

**Benefits:**
- No getters/setters needed
- Handlers don't need updates
- Clear data flow (explicit parameters)
- Easier to test (pass test data directly)
- State remains where it's used

### Step 5: Testing Strategy

**Create:** `tests/test_mqtt_publisher.py`

**Test Coverage:**
1. Test all 9 core publish methods
2. Test all 14 helpers
3. Test state variable access
4. Test Home Assistant discovery config generation
5. Test sound option caching
6. Test overlay clearing logic
7. Mock dependencies (mqtt, home_assistant, schedule_service)

**Integration Tests:**
- Verify publish methods called by PulseAssistant still work
- Verify state synchronization between classes
- Verify MQTT messages have correct format

### Step 6: Migration Checklist

**Pre-migration:**
- [ ] Run full test suite (baseline)
- [ ] Document current MQTT message formats
- [ ] Review all call sites in pulse-assistant.py

**During migration:**
- [ ] Create mqtt_publisher.py with all methods
- [ ] Add comprehensive docstrings
- [ ] Update PulseAssistant to use publisher
- [ ] Update all call sites (search/replace)
- [ ] Add getters/setters for state variables
- [ ] Update handler methods to use publisher

**Post-migration:**
- [ ] Run full test suite (verify no breakage)
- [ ] Add unit tests for mqtt_publisher
- [ ] Run integration tests
- [ ] Verify MQTT messages unchanged
- [ ] Check lint/format (ruff, black)
- [ ] Update line counts

## Risk Assessment

### High Risk Areas

1. **State Variable Access** - Handlers accessing `self._*` variables
   - **Mitigation:** Add getters/setters, comprehensive testing

2. **Async Methods** - `_publish_light_overlay()` is async
   - **Mitigation:** Preserve async/await properly, test async flows

3. **Callback Dependencies** - Some methods schedule callbacks
   - **Mitigation:** Keep event loop reference, test callback scheduling

4. **MQTT Message Formats** - Changes could break Home Assistant integration
   - **Mitigation:** Document formats, verify unchanged, integration tests

### Medium Risk Areas

5. **Dependency Injection** - Publisher needs 7 dependencies
   - **Mitigation:** Clear constructor, type hints, validation

6. **Call Site Updates** - 50+ locations need updating
   - **Mitigation:** Systematic search/replace, verify with tests

### Low Risk Areas

7. **Import Changes** - New module import
   - **Mitigation:** Standard Python import, no issues expected

## Success Criteria

- [ ] All tests passing (250+ tests)
- [ ] Line count reduced: pulse-assistant.py ~2,700 lines (from 3,197)
- [ ] New module created: mqtt_publisher.py ~500 lines
- [ ] No MQTT message format changes
- [ ] No behavioral changes
- [ ] Ruff/Black/Bandit passing
- [ ] Test coverage maintained or improved

## Rollback Plan

If issues arise:
1. Revert commit
2. Review error logs
3. Fix issues incrementally
4. Re-test before re-committing

## Timeline Estimate

- Step 1-2: Create module structure and extract methods (2-3 hours)
- Step 3: Update PulseAssistant (1 hour)
- Step 4: Handle state variables (1 hour)
- Step 5: Write tests (2-3 hours)
- Step 6: Migration and validation (1-2 hours)

**Total: 7-10 hours of focused work**

## Dependencies on Future Phases

Phase 2 (Preference Manager) will:
- Take over preference-related state
- Take over preference command handlers
- Reduce publisher responsibilities

Phase 5 (Schedule Command Processor) will:
- Take over schedule command handling
- Reduce publisher's schedule-related state

## Notes

- This extraction is conservative - only publish logic moves
- Handlers stay in PulseAssistant (phases 2-5)
- Publisher remains stateful (necessary for current architecture)
- Future phases will reduce publisher's state further

## Review Checklist

Before starting implementation:
- [ ] Plan reviewed and approved
- [ ] All risks identified and mitigations clear
- [ ] Test strategy comprehensive
- [ ] Dependencies understood
- [ ] Rollback plan in place
- [ ] Timeline realistic
