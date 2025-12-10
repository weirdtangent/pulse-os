"""Simple routine definitions and execution helpers."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Routine:
    slug: str
    label: str
    description: str
    scene_id: str | None = None
    overlay_hint: str | None = None


def _default_scene(key: str, fallback: str) -> str | None:
    value = os.environ.get(key)
    if value is None:
        return fallback
    trimmed = value.strip()
    return trimmed or None


def default_routines() -> list[Routine]:
    """Return built-in routines with optional scene overrides from env."""
    return [
        Routine(
            slug="routine.morning",
            label="Morning",
            description="Warm lights on, morning scene.",
            scene_id=_default_scene("PULSE_ROUTINE_SCENE_MORNING", "scene.morning"),
            overlay_hint="Morning routine running",
        ),
        Routine(
            slug="routine.leaving",
            label="Leaving",
            description="Turn everything off when you head out.",
            scene_id=_default_scene("PULSE_ROUTINE_SCENE_LEAVING", "scene.leaving"),
            overlay_hint="Goodbye routine running",
        ),
        Routine(
            slug="routine.movie",
            label="Movie",
            description="Dim the lights and start movie time.",
            scene_id=_default_scene("PULSE_ROUTINE_SCENE_MOVIE", "scene.movie_time"),
            overlay_hint="Movie mode enabled",
        ),
    ]


class RoutineEngine:
    """Execute named routines (typically via Home Assistant scenes)."""

    def __init__(self, routines: Iterable[Routine]) -> None:
        self._routines = {routine.slug: routine for routine in routines}

    def prompt_entries(self) -> list[dict[str, str]]:
        return [{"slug": routine.slug, "description": routine.description} for routine in self._routines.values()]

    def overlay_entries(self) -> list[dict[str, str]]:
        return [
            {"slug": routine.slug, "label": routine.label, "description": routine.description}
            for routine in self._routines.values()
        ]

    async def execute(self, tokens: Iterable[str], ha_client) -> list[str]:
        executed: list[str] = []
        for token in tokens:
            slug = token.split(":", 1)[0].strip()
            routine = self._routines.get(slug)
            if not routine:
                continue
            if await self._run_routine(routine, ha_client):
                executed.append(slug)
        return executed

    async def _run_routine(self, routine: Routine, ha_client) -> bool:
        if routine.scene_id and ha_client:
            await ha_client.activate_scene(routine.scene_id)
            return True
        return False
