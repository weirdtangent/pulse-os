from __future__ import annotations

import wave
from pathlib import Path

from pulse.sound_library import SoundLibrary, SoundSettings


def _write_silence_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes((0).to_bytes(2, byteorder="little", signed=True))


def test_resolve_builtin_default() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    library = SoundLibrary(built_in_dir=repo_root / "assets" / "sounds")
    settings = SoundSettings.with_defaults()

    path = library.resolve_with_default(None, kind="alarm", settings=settings)

    assert path is not None
    assert path.exists()


def test_resolve_custom_overrides_builtin(tmp_path: Path) -> None:
    custom_file = tmp_path / "custom-tone.wav"
    _write_silence_wav(custom_file)

    repo_root = Path(__file__).resolve().parents[1]
    library = SoundLibrary(custom_dir=tmp_path, built_in_dir=repo_root / "assets" / "sounds")
    settings = SoundSettings.with_defaults(default_alarm="alarm-digital-rise")

    path = library.resolve_with_default("custom-tone", kind="alarm", settings=settings)

    assert path == custom_file
