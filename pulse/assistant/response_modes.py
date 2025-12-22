"""Helpers for selecting Home Assistant response behavior."""

from __future__ import annotations

from collections.abc import Iterable


def select_ha_response(
    mode: str | None, executed_actions: Iterable[str], base_response: str | None
) -> tuple[str | None, bool]:
    """Choose the spoken response and whether to play a tone for HA actions.

    Returns (response_text, play_tone_flag).
    """
    actions = list(executed_actions)
    if not any(action.startswith("ha.") for action in actions):
        return base_response, False

    normalized_mode = (mode or "full").strip().lower()
    if normalized_mode == "none":
        return None, False
    if normalized_mode == "tone":
        return None, True
    if normalized_mode == "minimal":
        return "Ok.", False
    # Default to full response
    return base_response, False
