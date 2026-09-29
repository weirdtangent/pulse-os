"""The stylesheet's own `--overlay-*` defaults, read from the stylesheet.

Parsed rather than restated so the two cannot drift: assets/overlay/overlay.css is the
single place a token's default is written down, and a second copy in Python would be
wrong the first time anyone edited one and not the other.

These are needed in Python because the rendered theme block has to carry the COMPLETE
token set, not just the active theme's overrides -- see _theme_css in overlay.py for
what goes wrong when it carries a diff.
"""

from __future__ import annotations

import re

from pulse.overlay_assets import OVERLAY_CSS

# This module exists to export one constant. Declared explicitly because a module-level
# name that is only read from OTHER modules reads as dead code to intra-file analysis --
# CodeQL flagged it as an unused global.
__all__ = ["STYLE_TOKEN_DEFAULTS"]

_ROOT_BLOCK_RE = re.compile(r":root\s*\{(.*?)\n\}", re.DOTALL)
_DECLARATION_RE = re.compile(r"^\s*(--overlay-[a-z0-9-]+)\s*:\s*(.+?);\s*$", re.MULTILINE)


def _parse_defaults() -> dict[str, str]:
    """Declarations from the stylesheet's FIRST :root block, in source order."""
    match = _ROOT_BLOCK_RE.search(OVERLAY_CSS)
    if not match:  # pragma: no cover - the sheet always has one
        return {}
    # Comments first: a commented-out declaration is documentation, not a default.
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    return dict(_DECLARATION_RE.findall(body))


STYLE_TOKEN_DEFAULTS: dict[str, str] = _parse_defaults()
