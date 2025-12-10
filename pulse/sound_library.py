"""Sound catalog and resolution helpers for built-in and custom sounds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SoundKind = Literal["alarm", "timer", "reminder", "notification"]

_DEFAULT_CUSTOM_DIR = Path.home() / ".local" / "share" / "pulse" / "sounds"
_ALLOWED_EXTENSIONS = {".wav", ".ogg"}


@dataclass(frozen=True)
class SoundInfo:
    sound_id: str
    label: str
    path: Path
    kinds: tuple[SoundKind, ...]
    built_in: bool


@dataclass(frozen=True)
class SoundSettings:
    default_alarm: str
    default_timer: str
    default_reminder: str
    default_notification: str
    custom_dir: Path = _DEFAULT_CUSTOM_DIR

    @classmethod
    def with_defaults(
        cls,
        *,
        custom_dir: Path | None = None,
        default_alarm: str = "alarm-digital-rise",
        default_timer: str = "timer-woodblock",
        default_reminder: str = "reminder-marimba",
        default_notification: str = "notify-soft-chime",
    ) -> SoundSettings:
        return cls(
            default_alarm=default_alarm,
            default_timer=default_timer,
            default_reminder=default_reminder,
            default_notification=default_notification,
            custom_dir=custom_dir or _DEFAULT_CUSTOM_DIR,
        )


class SoundLibrary:
    """Resolve sound identifiers to concrete files."""

    def __init__(
        self,
        *,
        custom_dir: Path | None = None,
        built_in_dir: Path | None = None,
    ) -> None:
        self.custom_dir = custom_dir or _DEFAULT_CUSTOM_DIR
        self.built_in_dir = built_in_dir or Path(__file__).resolve().parent.parent / "assets" / "sounds"
        self.manifest_path = self.built_in_dir / "pack" / "manifest.json"
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> list[dict[str, object]]:
        if not self.manifest_path.exists():
            return []
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def built_in_sounds(self) -> list[SoundInfo]:
        sounds: list[SoundInfo] = []
        for entry in self._manifest:
            sound_id = str(entry.get("id") or "")
            filename = entry.get("filename")
            label = str(entry.get("label") or sound_id)
            kinds_raw = entry.get("kinds") or []
            if not sound_id or not isinstance(filename, str):
                continue
            path = (self.built_in_dir / "pack" / filename).resolve()
            if not path.exists():
                continue
            kinds: tuple[SoundKind, ...] = tuple(
                kind for kind in kinds_raw if kind in {"alarm", "timer", "reminder", "notification"}
            )  # type: ignore[assignment]
            sounds.append(
                SoundInfo(
                    sound_id=sound_id,
                    label=label,
                    path=path,
                    kinds=kinds or ("alarm", "timer", "reminder", "notification"),
                    built_in=True,
                )
            )
        return sounds

    def custom_sounds(self) -> list[SoundInfo]:
        sounds: list[SoundInfo] = []
        if not self.custom_dir.exists():
            return sounds
        for candidate in sorted(self.custom_dir.glob("*")):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            sound_id = candidate.stem
            sounds.append(
                SoundInfo(
                    sound_id=sound_id,
                    label=sound_id.replace("_", " ").replace("-", " ").title(),
                    path=candidate.resolve(),
                    kinds=("alarm", "timer", "reminder", "notification"),
                    built_in=False,
                )
            )
        return sounds

    def _find_built_in(self, sound_id: str) -> SoundInfo | None:
        for info in self.built_in_sounds():
            if info.sound_id == sound_id:
                return info
        return None

    def _find_custom(self, sound_id: str) -> SoundInfo | None:
        for info in self.custom_sounds():
            if info.sound_id == sound_id:
                return info
        return None

    def resolve_sound(self, sound_id: str | None, *, kind: SoundKind | None = None) -> SoundInfo | None:
        """Resolve a sound id or path into a SoundInfo."""
        if not sound_id:
            return None

        candidate_path = Path(sound_id)
        if candidate_path.exists() and candidate_path.suffix.lower() in _ALLOWED_EXTENSIONS:
            return SoundInfo(
                sound_id=sound_id,
                label=candidate_path.stem,
                path=candidate_path.resolve(),
                kinds=(kind or "alarm",),
                built_in=False,
            )

        info = self._find_custom(sound_id)
        if info:
            return info

        info = self._find_built_in(sound_id)
        if info and (not kind or kind in info.kinds):
            return info
        return None

    def resolve_with_default(self, sound_id: str | None, *, kind: SoundKind, settings: SoundSettings) -> Path | None:
        """Resolve a sound id, falling back to defaults for the requested kind."""
        info = self.resolve_sound(sound_id, kind=kind)
        if info:
            return info.path
        fallback_id = {
            "alarm": settings.default_alarm,
            "timer": settings.default_timer,
            "reminder": settings.default_reminder,
            "notification": settings.default_notification,
        }.get(kind)
        fallback = self.resolve_sound(fallback_id, kind=kind)
        return fallback.path if fallback else None

    def ensure_custom_dir(self) -> None:
        """Make sure the custom directory exists."""
        self.custom_dir.mkdir(parents=True, exist_ok=True)
