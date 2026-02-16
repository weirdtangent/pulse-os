#!/usr/bin/env python3
"""Slice the bundled weather sprite sheet into individual icons."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - manual utility
    raise SystemExit("Pillow is required: pip install Pillow") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
SPRITE_PATH = REPO_ROOT / "assets" / "weather" / "flat-2022478_1920.png"
OUTPUT_DIR = REPO_ROOT / "assets" / "weather" / "icons"
MIN_SEGMENT = 40


@dataclass(frozen=True)
class Segment:
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start

    def expand(self, pad: int, limit: int) -> tuple[int, int]:
        return max(0, self.start - pad), min(limit, self.end + pad)


TARGET_ICONS: dict[str, tuple[int, int]] = {
    "sunny": (0, 0),
    "partly_cloudy": (0, 1),
    "mostly_cloudy": (2, 1),
    "cloudy": (2, 0),
    "fog": (6, 0),
    "drizzle": (3, 0),
    "rain": (4, 2),
    "downpour": (4, 0),
    "sleet": (8, 2),
    "snow": (8, 1),
    "thunder": (0, 5),
}


def _scan_segments(mask: Image.Image, length: int, getter) -> list[Segment]:
    segments: list[Segment] = []
    inside = False
    start = 0
    for idx in range(length):
        filled = getter(idx)
        if filled and not inside:
            inside = True
            start = idx
        elif not filled and inside:
            inside = False
            if idx - start >= MIN_SEGMENT:
                segments.append(Segment(start, idx))
    if inside and length - start >= MIN_SEGMENT:
        segments.append(Segment(start, length))
    return segments


def _row_segments(alpha: Image.Image, width: int, height: int) -> list[Segment]:
    def row_has(y: int) -> bool:
        return any(alpha.getpixel((x, y)) > 0 for x in range(width))

    return _scan_segments(alpha, height, row_has)


def _column_segments(alpha: Image.Image, width: int, y0: int, y1: int) -> list[Segment]:
    def col_has(x: int) -> bool:
        return any(alpha.getpixel((x, y)) > 0 for y in range(y0, y1))

    return _scan_segments(alpha, width, col_has)


def _load_sprite() -> tuple[Image.Image, Image.Image]:
    if not SPRITE_PATH.exists():
        raise SystemExit(f"Sprite not found: {SPRITE_PATH}")
    image = Image.open(SPRITE_PATH).convert("RGBA")
    return image, image.split()[-1]


def _build_grid(alpha: Image.Image, width: int, height: int) -> dict[tuple[int, int], tuple[int, int, int, int]]:
    rows = _row_segments(alpha, width, height)
    grid: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for row_idx, row in enumerate(rows):
        cols = _column_segments(alpha, width, row.start, row.end)
        for col_idx, col in enumerate(cols):
            x0, x1 = col.expand(6, width)
            y0, y1 = row.expand(6, height)
            grid[(row_idx, col_idx)] = (x0, y0, x1, y1)
    return grid


def _extract_icons() -> int:
    image, alpha = _load_sprite()
    width, height = image.size
    grid = _build_grid(alpha, width, height)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUTPUT_DIR.glob("*.png"):
        path.unlink()
    written = 0
    for name, coords in TARGET_ICONS.items():
        key = (coords[0], coords[1])
        if key not in grid:
            print(f"[warn] missing grid cell for {name} at {key}", file=sys.stderr)
            continue
        box = grid[key]
        tile = image.crop(box)
        dest = OUTPUT_DIR / f"{name}.png"
        tile.save(dest)
        written += 1
        print(f"[ok] wrote {dest.relative_to(REPO_ROOT)}")
    return written


def main() -> None:
    count = _extract_icons()
    if count == 0:
        raise SystemExit("No icons were written.")
    print(f"Generated {count} weather icon(s).")


if __name__ == "__main__":  # pragma: no cover - manual utility
    main()
