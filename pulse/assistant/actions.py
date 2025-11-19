"""Action parsing and execution helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ActionDefinition:
    slug: str
    description: str
    type: str
    topic: str
    payload: str
    retain: bool = False
    qos: int = 0

    def to_prompt_dict(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "description": self.description,
        }


def load_action_definitions(action_file: Path | None, inline_json: str | None) -> list[ActionDefinition]:
    """Load action definitions from a JSON file or inline JSON string."""
    candidates: list[dict] = []
    if action_file and action_file.exists():
        try:
            candidates.extend(_ensure_list(json.loads(action_file.read_text(encoding="utf-8"))))
        except Exception:  # pylint: disable=broad-except
            pass

    if inline_json:
        try:
            candidates.extend(_ensure_list(json.loads(inline_json)))
        except Exception:  # pylint: disable=broad-except
            pass

    definitions: list[ActionDefinition] = []
    for candidate in candidates:
        slug = str(candidate.get("slug") or "").strip()
        topic = str(candidate.get("topic") or "").strip()
        payload = candidate.get("payload")
        if not slug or not topic or payload is None:
            continue

        description = str(candidate.get("description") or slug)
        action_type = (candidate.get("type") or "mqtt").lower()
        if action_type != "mqtt":
            # Only MQTT actions are supported for now
            continue

        definitions.append(
            ActionDefinition(
                slug=slug,
                description=description,
                type=action_type,
                topic=topic,
                payload=json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload),
                retain=bool(candidate.get("retain", False)),
                qos=int(candidate.get("qos", 0)),
            )
        )
    return definitions


def _ensure_list(value) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


class ActionEngine:
    """Execute assistant actions (currently MQTT only)."""

    def __init__(self, definitions: Iterable[ActionDefinition]) -> None:
        self._definitions = {definition.slug: definition for definition in definitions}

    def describe_for_prompt(self) -> list[dict[str, str]]:
        return [definition.to_prompt_dict() for definition in self._definitions.values()]

    def execute(self, slugs: Iterable[str], mqtt_client) -> list[str]:
        executed: list[str] = []
        if mqtt_client is None:
            return executed

        seen: set[str] = set()
        for slug in slugs:
            if slug in seen:
                continue
            seen.add(slug)
            definition = self._definitions.get(slug)
            if not definition:
                continue
            if definition.type == "mqtt":
                mqtt_client.publish(
                    definition.topic,
                    definition.payload,
                    retain=definition.retain,
                    qos=definition.qos,
                )
                executed.append(slug)
        return executed
