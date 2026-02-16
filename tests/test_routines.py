"""Tests for routines module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pulse.assistant.routines import Routine, RoutineEngine, _default_scene, default_routines

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Routine dataclass
# ---------------------------------------------------------------------------


class TestRoutine:
    def test_creation(self):
        r = Routine(slug="routine.test", label="Test", description="A test routine")
        assert r.slug == "routine.test"
        assert r.label == "Test"
        assert r.scene_id is None
        assert r.overlay_hint is None

    def test_frozen(self):
        r = Routine(slug="routine.test", label="Test", description="desc")
        with pytest.raises(AttributeError):
            r.slug = "other"  # type: ignore[misc]

    def test_with_scene(self):
        r = Routine(slug="routine.test", label="Test", description="desc", scene_id="scene.test")
        assert r.scene_id == "scene.test"


# ---------------------------------------------------------------------------
# _default_scene helper
# ---------------------------------------------------------------------------


class TestDefaultScene:
    def test_returns_fallback_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _default_scene("MISSING_KEY", "scene.fallback") == "scene.fallback"

    def test_returns_env_value(self):
        with patch.dict("os.environ", {"PULSE_TEST_SCENE": "scene.custom"}):
            assert _default_scene("PULSE_TEST_SCENE", "scene.fallback") == "scene.custom"

    def test_returns_none_for_empty_env(self):
        with patch.dict("os.environ", {"PULSE_TEST_SCENE": ""}):
            assert _default_scene("PULSE_TEST_SCENE", "scene.fallback") is None

    def test_strips_whitespace(self):
        with patch.dict("os.environ", {"PULSE_TEST_SCENE": "  scene.spaces  "}):
            assert _default_scene("PULSE_TEST_SCENE", "scene.fallback") == "scene.spaces"


# ---------------------------------------------------------------------------
# default_routines
# ---------------------------------------------------------------------------


class TestDefaultRoutines:
    def test_returns_three_routines(self):
        routines = default_routines()
        assert len(routines) == 3

    def test_routine_slugs(self):
        routines = default_routines()
        slugs = [r.slug for r in routines]
        assert "routine.morning" in slugs
        assert "routine.leaving" in slugs
        assert "routine.movie" in slugs

    def test_all_have_scene_ids(self):
        routines = default_routines()
        for r in routines:
            assert r.scene_id is not None

    def test_all_have_overlay_hints(self):
        routines = default_routines()
        for r in routines:
            assert r.overlay_hint is not None


# ---------------------------------------------------------------------------
# RoutineEngine
# ---------------------------------------------------------------------------


class TestRoutineEngine:
    def _make_engine(self):
        routines = [
            Routine(slug="routine.morning", label="Morning", description="Morning routine", scene_id="scene.morning"),
            Routine(slug="routine.movie", label="Movie", description="Movie mode", scene_id="scene.movie"),
            Routine(slug="routine.noscene", label="NoScene", description="No scene"),
        ]
        return RoutineEngine(routines)

    def test_prompt_entries(self):
        engine = self._make_engine()
        entries = engine.prompt_entries()
        assert len(entries) == 3
        slugs = [e["slug"] for e in entries]
        assert "routine.morning" in slugs
        assert "routine.movie" in slugs
        assert all("description" in e for e in entries)

    def test_overlay_entries(self):
        engine = self._make_engine()
        entries = engine.overlay_entries()
        assert len(entries) == 3
        assert all("label" in e for e in entries)
        assert all("slug" in e for e in entries)

    async def test_execute_known_routine(self):
        engine = self._make_engine()
        ha_client = AsyncMock()
        executed = await engine.execute(["routine.morning"], ha_client)
        assert executed == ["routine.morning"]
        ha_client.activate_scene.assert_awaited_once_with("scene.morning")

    async def test_execute_unknown_routine_skipped(self):
        engine = self._make_engine()
        ha_client = AsyncMock()
        executed = await engine.execute(["routine.unknown"], ha_client)
        assert executed == []
        ha_client.activate_scene.assert_not_awaited()

    async def test_execute_multiple_routines(self):
        engine = self._make_engine()
        ha_client = AsyncMock()
        executed = await engine.execute(["routine.morning", "routine.movie"], ha_client)
        assert executed == ["routine.morning", "routine.movie"]
        assert ha_client.activate_scene.await_count == 2

    async def test_execute_routine_without_scene(self):
        engine = self._make_engine()
        ha_client = AsyncMock()
        executed = await engine.execute(["routine.noscene"], ha_client)
        assert executed == []

    async def test_execute_routine_without_ha_client(self):
        engine = self._make_engine()
        executed = await engine.execute(["routine.morning"], None)
        assert executed == []

    async def test_execute_strips_colon_suffix(self):
        engine = self._make_engine()
        ha_client = AsyncMock()
        executed = await engine.execute(["routine.morning:extra"], ha_client)
        assert executed == ["routine.morning"]
