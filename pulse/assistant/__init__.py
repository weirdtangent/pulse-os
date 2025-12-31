"""
Voice assistant implementation with LLM and Home Assistant integration

This package provides the core voice assistant functionality for PulseOS including:

- Wake word detection: Openwakeword integration for hands-free activation
- Speech recognition: Wyoming protocol (faster-whisper) for local transcription
- LLM processing: Action parsing and intent detection (OpenAI, Anthropic, or local Ollama)
- Speech synthesis: Piper TTS for natural voice output
- Home Assistant integration: Entity control, service calls, Assist pipeline
- Scheduling: Alarms, timers, reminders with MQTT and local persistence
- Information services: Weather, news, sports via public APIs
- Calendar sync: iCal/CalDAV integration for event awareness
- Media control: MPRIS and Spotify control
- Routines: Scene activation and automation workflows

Key modules:
- config: Configuration management from environment variables
- actions: Action parsing and execution (lights, volume, timers, etc.)
- scheduler: Timer and reminder scheduling with Home Assistant integration
- llm: LLM interaction with tool use for action extraction
- info_service: Real-time information answers (weather, news, sports)
- routines: Routine/scene execution
"""

from __future__ import annotations

__all__ = [
    "config",
    "actions",
    "audio",
    "calendar_sync",
    "routines",
    "llm",
    "mqtt",
]
