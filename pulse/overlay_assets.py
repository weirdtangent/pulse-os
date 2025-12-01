"""Static CSS/JS assets for the Pulse overlay renderer."""

from __future__ import annotations

from pathlib import Path


def _get_assets_dir() -> Path:
    """Return the path to the assets directory."""
    # Resolve from this file's location: pulse/overlay_assets.py -> ../assets/overlay/
    return Path(__file__).resolve().parent.parent / "assets" / "overlay"


def _load_css() -> str:
    """Load the CSS file from assets."""
    css_path = _get_assets_dir() / "overlay.css"
    return css_path.read_text(encoding="utf-8").strip()


def _load_js() -> str:
    """Load the JavaScript file from assets."""
    js_path = _get_assets_dir() / "overlay.js"
    return js_path.read_text(encoding="utf-8").strip()


# Load assets at module import time
OVERLAY_CSS = _load_css()
OVERLAY_JS = _load_js()
