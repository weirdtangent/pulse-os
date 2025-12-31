"""
Static asset loader for overlay UI

Loads and caches CSS and JavaScript files from the assets/overlay/ directory.
Assets are loaded once at module import time and exposed as module-level constants:
- OVERLAY_CSS: Overlay styling
- OVERLAY_JS: Client-side interactivity

Used by overlay.py to inject static assets into the rendered HTML.
"""

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
