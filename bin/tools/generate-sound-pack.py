#!/usr/bin/env python3
"""Generate the curated PulseOS sound pack."""

from __future__ import annotations

import argparse
import json
import math
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "assets" / "sounds" / "pack"
SAMPLE_RATE = 48_000
MAX_AMPLITUDE = 28_000


@dataclass(frozen=True)
class Segment:
    duration: float
    freqs: tuple[float, ...]
    envelope: tuple[float, float] = (0.01, 0.2)  # attack, decay fraction
    gain: float = 1.0
    shimmer: bool = False


@dataclass(frozen=True)
class SoundSpec:
    sound_id: str
    label: str
    filename: str
    kinds: tuple[str, ...]
    segments: tuple[Segment, ...]


SOUNDS: tuple[SoundSpec, ...] = (
    SoundSpec(
        sound_id="notify-soft-chime",
        label="Soft Chime",
        filename="notify-soft-chime.wav",
        kinds=("notification",),
        segments=(Segment(0.35, (880, 1320), envelope=(0.02, 0.35), gain=0.7),),
    ),
    SoundSpec(
        sound_id="notify-pleasant-trill",
        label="Pleasant Trill",
        filename="notify-pleasant-trill.wav",
        kinds=("notification", "reminder"),
        segments=(
            Segment(0.18, (660, 990), envelope=(0.01, 0.25), gain=0.8),
            Segment(0.16, (880, 1320), envelope=(0.005, 0.25), gain=0.9, shimmer=True),
        ),
    ),
    SoundSpec(
        sound_id="notify-two-tone",
        label="Two-Tone",
        filename="notify-two-tone.wav",
        kinds=("notification",),
        segments=(
            Segment(0.14, (620,), envelope=(0.005, 0.25), gain=0.9),
            Segment(0.14, (780,), envelope=(0.005, 0.25), gain=0.9),
        ),
    ),
    SoundSpec(
        sound_id="notify-airy-pluck",
        label="Airy Pluck",
        filename="notify-airy-pluck.wav",
        kinds=("notification", "reminder"),
        segments=(Segment(0.28, (1040, 1550), envelope=(0.003, 0.3), gain=0.75, shimmer=True),),
    ),
    SoundSpec(
        sound_id="notify-glass-pop",
        label="Glass Pop",
        filename="notify-glass-pop.wav",
        kinds=("notification",),
        segments=(Segment(0.2, (980, 1470, 1960), envelope=(0.002, 0.32), gain=0.72),),
    ),
    SoundSpec(
        sound_id="alarm-digital-rise",
        label="Digital Rise",
        filename="alarm-digital-rise.wav",
        kinds=("alarm", "timer"),
        segments=(
            Segment(0.18, (620, 930), envelope=(0.01, 0.18), gain=0.95),
            Segment(0.18, (780, 1170), envelope=(0.01, 0.18), gain=0.95),
            Segment(0.18, (930, 1395), envelope=(0.01, 0.18), gain=0.95),
        ),
    ),
    SoundSpec(
        sound_id="alarm-bright-triple",
        label="Bright Triple",
        filename="alarm-bright-triple.wav",
        kinds=("alarm", "timer"),
        segments=(
            Segment(0.18, (880,), envelope=(0.005, 0.2), gain=0.9),
            Segment(0.18, (1040,), envelope=(0.005, 0.2), gain=0.9),
            Segment(0.22, (1240,), envelope=(0.005, 0.22), gain=0.95),
        ),
    ),
    SoundSpec(
        sound_id="alarm-woodpecker",
        label="Woodpecker",
        filename="alarm-woodpecker.wav",
        kinds=("alarm", "timer"),
        segments=(
            Segment(0.1, (820,), envelope=(0.002, 0.15), gain=0.9),
            Segment(0.12, (720,), envelope=(0.002, 0.15), gain=0.9),
            Segment(0.12, (720,), envelope=(0.002, 0.15), gain=0.9),
            Segment(0.18, (680,), envelope=(0.003, 0.2), gain=0.9),
        ),
    ),
    SoundSpec(
        sound_id="alarm-pulse-sweep",
        label="Pulse Sweep",
        filename="alarm-pulse-sweep.wav",
        kinds=("alarm", "timer"),
        segments=(
            Segment(0.28, (640, 1280), envelope=(0.01, 0.25), gain=0.95),
            Segment(0.28, (720, 1440), envelope=(0.01, 0.25), gain=0.95, shimmer=True),
        ),
    ),
    SoundSpec(
        sound_id="alarm-analog-bell",
        label="Analog Bell",
        filename="alarm-analog-bell.wav",
        kinds=("alarm", "timer"),
        segments=(Segment(0.6, (660, 990, 1320), envelope=(0.02, 0.75), gain=0.9),),
    ),
    SoundSpec(
        sound_id="alarm-astro",
        label="Astro Beacon",
        filename="alarm-astro.wav",
        kinds=("alarm", "timer"),
        segments=(
            Segment(0.24, (520, 1560), envelope=(0.015, 0.35), gain=0.9),
            Segment(0.24, (620, 1860), envelope=(0.015, 0.35), gain=0.9, shimmer=True),
        ),
    ),
    SoundSpec(
        sound_id="alarm-buzzer-soft",
        label="Soft Buzzer",
        filename="alarm-buzzer-soft.wav",
        kinds=("alarm", "timer"),
        segments=(Segment(0.4, (360, 540), envelope=(0.005, 0.4), gain=0.85, shimmer=True),),
    ),
    SoundSpec(
        sound_id="timer-woodblock",
        label="Woodblock",
        filename="timer-woodblock.wav",
        kinds=("timer", "notification"),
        segments=(Segment(0.18, (520, 1040), envelope=(0.003, 0.2), gain=0.95),),
    ),
    SoundSpec(
        sound_id="timer-mallet-duo",
        label="Mallet Duo",
        filename="timer-mallet-duo.wav",
        kinds=("timer", "reminder"),
        segments=(
            Segment(0.16, (620, 930), envelope=(0.003, 0.2), gain=0.9),
            Segment(0.18, (740, 1110), envelope=(0.003, 0.25), gain=0.9),
        ),
    ),
    SoundSpec(
        sound_id="timer-interval-beep",
        label="Interval Beep",
        filename="timer-interval-beep.wav",
        kinds=("timer",),
        segments=(
            Segment(0.12, (820,), envelope=(0.002, 0.2), gain=0.9),
            Segment(0.12, (820,), envelope=(0.002, 0.2), gain=0.9),
        ),
    ),
    SoundSpec(
        sound_id="reminder-marimba",
        label="Marimba",
        filename="reminder-marimba.wav",
        kinds=("reminder", "notification"),
        segments=(
            Segment(0.22, (1040, 1560), envelope=(0.004, 0.25), gain=0.85, shimmer=True),
            Segment(0.22, (880, 1320), envelope=(0.004, 0.25), gain=0.75),
        ),
    ),
    SoundSpec(
        sound_id="reminder-glass-harp",
        label="Glass Harp",
        filename="reminder-glass-harp.wav",
        kinds=("reminder",),
        segments=(Segment(0.5, (1320, 1980), envelope=(0.01, 0.65), gain=0.8, shimmer=True),),
    ),
    SoundSpec(
        sound_id="reminder-warm-chord",
        label="Warm Chord",
        filename="reminder-warm-chord.wav",
        kinds=("reminder",),
        segments=(Segment(0.48, (520, 660, 880), envelope=(0.01, 0.6), gain=0.85),),
    ),
    SoundSpec(
        sound_id="reminder-bell-duo",
        label="Bell Duo",
        filename="reminder-bell-duo.wav",
        kinds=("reminder",),
        segments=(
            Segment(0.35, (780, 1560), envelope=(0.006, 0.5), gain=0.85),
            Segment(0.35, (660, 990), envelope=(0.006, 0.5), gain=0.8),
        ),
    ),
    SoundSpec(
        sound_id="reminder-soft-arpeggio",
        label="Soft Arpeggio",
        filename="reminder-soft-arpeggio.wav",
        kinds=("reminder", "notification"),
        segments=(
            Segment(0.18, (660,), envelope=(0.003, 0.22), gain=0.75),
            Segment(0.18, (880,), envelope=(0.003, 0.22), gain=0.78),
            Segment(0.18, (1100,), envelope=(0.003, 0.22), gain=0.82),
        ),
    ),
    SoundSpec(
        sound_id="alarm-sonar",
        label="Sonar Ping",
        filename="alarm-sonar.wav",
        kinds=("alarm", "timer"),
        segments=(Segment(0.4, (420, 1260), envelope=(0.01, 0.6), gain=0.9),),
    ),
    SoundSpec(
        sound_id="notify-short-pluck",
        label="Short Pluck",
        filename="notify-short-pluck.wav",
        kinds=("notification",),
        segments=(Segment(0.16, (920,), envelope=(0.002, 0.22), gain=0.85),),
    ),
    SoundSpec(
        sound_id="reminder-uplift",
        label="Uplift",
        filename="reminder-uplift.wav",
        kinds=("reminder", "notification"),
        segments=(
            Segment(0.16, (540,), envelope=(0.003, 0.2), gain=0.72),
            Segment(0.16, (720,), envelope=(0.003, 0.2), gain=0.78),
            Segment(0.2, (980,), envelope=(0.003, 0.22), gain=0.82, shimmer=True),
        ),
    ),
)


def _render_segment(segment: Segment, frames: list[int]) -> None:
    total_samples = max(1, int(segment.duration * SAMPLE_RATE))
    attack_samples = max(1, int(total_samples * segment.envelope[0]))
    decay_samples = max(1, int(total_samples * segment.envelope[1]))
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        env = 1.0
        if i < attack_samples:
            env *= i / attack_samples
        if i > total_samples - decay_samples:
            env *= max(0.0, (total_samples - i) / decay_samples)
        sample_val = 0.0
        for idx, freq in enumerate(segment.freqs):
            angle = 2 * math.pi * freq * t
            mod = 1.0
            if segment.shimmer and idx % 2 == 0:
                mod += 0.08 * math.sin(2 * math.pi * 6.5 * t)
            sample_val += math.sin(angle) * mod
        sample_val /= max(1, len(segment.freqs))
        sample_val *= env * segment.gain
        frames.append(sample_val)


def _normalize(frames: Iterable[float]) -> list[int]:
    vals = list(frames)
    if not vals:
        return []
    peak = max(abs(v) for v in vals) or 1.0
    scale = min(1.0, 1.0 / peak)
    return [int(max(-1.0, min(1.0, v * scale)) * MAX_AMPLITUDE) for v in vals]


def render_sound(spec: SoundSpec, output_dir: Path) -> Path:
    frames: list[int] = []
    for segment in spec.segments:
        _render_segment(segment, frames)
    normalized = _normalize(frames)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / spec.filename
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        for sample in normalized:
            wav_file.writeframes(sample.to_bytes(2, byteorder="little", signed=True))
    return path


def write_manifest(output_dir: Path) -> Path:
    manifest = [
        {
            "id": spec.sound_id,
            "label": spec.label,
            "filename": spec.filename,
            "kinds": list(spec.kinds),
        }
        for spec in SOUNDS
    ]
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate curated PulseOS sound pack")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory for sound pack")
    args = parser.parse_args()

    output_dir = args.output.resolve()
    generated = []
    for spec in SOUNDS:
        generated.append(render_sound(spec, output_dir))
    manifest_path = write_manifest(output_dir)
    print(f"Generated {len(generated)} sounds into {output_dir}")  # noqa: T201
    print(f"Wrote manifest: {manifest_path}")  # noqa: T201


if __name__ == "__main__":
    main()
