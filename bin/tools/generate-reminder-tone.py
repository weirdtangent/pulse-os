#!/usr/bin/env python3
"""Generate the bundled reminder tone used for reminders and calendar events."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_audio():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from pulse import audio  # pylint: disable=import-outside-toplevel

    return audio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=REPO_ROOT / "assets" / "sounds" / "reminder.wav",
        help="Destination path for the generated WAV file",
    )
    return parser.parse_args()


def main() -> None:
    audio = _load_audio()
    args = parse_args()
    output_path = args.output.expanduser().resolve()
    result = audio.render_reminder_sample(output_path)
    if not result:
        print(f"Failed to write sample to {output_path}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()

