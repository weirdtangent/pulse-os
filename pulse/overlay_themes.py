"""Named overlay themes.

A theme is a set of `--overlay-*` token overrides and nothing else. It never adds a
CSS rule, because it cannot: the refresh loop in overlay_server.py copies custom
properties onto documentElement every couple of seconds but never replaces the
stylesheet, so a token change reaches a running kiosk within one poll while a new rule
does not land until the page reloads. Themes being pure token sets is what makes
switching one instant on a wall display instead of a reload nobody asked for.

`original` is deliberately empty: assets/overlay/overlay.css declares the original
look in its own :root, so the default theme is "change nothing" and every other theme
is a diff against it. That also means a theme only has to name what it actually
changes -- which is the difference between a file that grows to twenty themes and one
that stops at three.

Status colours (positive/negative/caution/fault/severe/watch) are optional. A theme
that leaves them out inherits the stock signal palette, which is usually right: they
are the only at-a-glance meaning the notification bar carries, and a theme should have
to want them changed rather than get it by accident. Themes with a palette strong
enough that the stock reds and ambers would look pasted on do override them.

Fonts here name faces installed by config/apt/manual-packages.txt. A face that is
missing renders as the fallback with no warning at all, so a theme that wants a new one
means adding the package in the same change; a test checks the two agree.
"""

from __future__ import annotations

DEFAULT_THEME = "original"

# Appended to every theme's font stack so a face missing a glyph -- or missing outright
# -- still lands somewhere sensible rather than on the browser's generic.
_FALLBACK = '"DejaVu Sans", "Liberation Sans", sans-serif, "Noto Color Emoji"'

# The faces the fleet installs, grouped by what they look like.
#
# A stock Debian image alone would not support eleven themes: Liberation Sans, Nimbus
# Sans and DejaVu Sans are all Helvetica/Arial-adjacent grotesques that never read as
# meaningfully different, which left most themes sharing one look. The ten font packages
# in config/apt/manual-packages.txt are what makes a face per theme possible, and they
# cost ~95MB against 106GB free.
_MONO = f'"JetBrains Mono", "DejaVu Sans Mono", monospace, {_FALLBACK}'
_CONDENSED = f'"IBM Plex Sans Condensed", "Liberation Sans Narrow", {_FALLBACK}'
_GEOMETRIC_ROUND = f'"Quicksand", "URW Gothic", {_FALLBACK}'
_GEOMETRIC_HEAVY = f'"League Spartan", "URW Gothic", {_FALLBACK}'
_HUMANIST = f'"Cabin", "Cantarell", {_FALLBACK}'
_MODERN = f'"Manrope", "Inter", {_FALLBACK}'
_PLEX = f'"IBM Plex Sans", {_FALLBACK}'
_GROTESQUE = f'"Nimbus Sans", {_FALLBACK}'  # Helvetica
_SERIF_WARM = f'"Vollkorn", "URW Bookman", serif, {_FALLBACK}'
_SERIF_CALM = f'"EB Garamond", "P052", serif, {_FALLBACK}'

# Seven-segment LCD, for the clock and nothing else. DSEG14 rather than DSEG7: seven
# segments cannot form an M, so DSEG7 renders a 12-hour "1:53 PM" as "1:53 Pn". Even
# DSEG14 turns ordinary prose into unreadable blocks, which is why this is only ever
# assigned to --overlay-clock-font-family.
_LCD = f'"DSEG14 Classic", "DSEG7 Classic", {_MONO}'


THEMES: dict[str, dict[str, str]] = {
    # The stylesheet's own :root. Empty on purpose -- see the module docstring.
    "original": {},
    # The palette the stock accent (#88C0D0) was borrowed from, finished properly:
    # cool, low-contrast, and the closest thing here to "invisible furniture".
    "nord": {
        "--overlay-tint-rgb": "216, 222, 233",
        "--overlay-text-color": "#eceff4",
        "--overlay-accent-color": "#88c0d0",
        "--overlay-accent-text": "#2e3440",
        # Humanist rather than another grotesque: softer terminals, and it carries
        # the palette's warmth better than Helvetica does.
        "--overlay-font-family": _HUMANIST,
        "--overlay-ambient-bg": "rgba(46, 52, 64, 0.62)",
        "--overlay-alert-bg": "rgba(46, 52, 64, 0.86)",
        "--overlay-surface-solid": "#3b4252",
        "--overlay-positive": "#a3be8c",
        "--overlay-negative": "#bf616a",
        "--overlay-caution": "#ebcb8b",
        "--overlay-fault": "#d08770",
        "--overlay-severe": "#a54c56",
        "--overlay-watch": "#ebcb8b",
        "--overlay-sleep-color": "#8a4a52",
    },
    # Neutral and quiet. For a room where the display should stop asking for attention:
    # softened chrome, a grey accent, and the only colour on screen is a real warning.
    "slate": {
        "--overlay-tint-rgb": "226, 232, 240",
        "--overlay-text-color": "#e8edf2",
        "--overlay-accent-color": "#9fb3c8",
        "--overlay-accent-text": "#10161d",
        # Quiet modern geometric. The brief is "stop asking for attention", and
        # Manrope is the least opinionated face here that still looks designed.
        "--overlay-font-family": _MODERN,
        "--overlay-ambient-bg": "rgba(17, 24, 32, 0.58)",
        "--overlay-alert-bg": "rgba(17, 24, 32, 0.84)",
        "--overlay-surface-solid": "#1b232d",
        "--overlay-blur-panel": "24px",
        "--overlay-shadow-card": "none",
    },
    # Greyscale, and that is the whole idea: with no chroma anywhere else, a weather
    # warning or a dead speaker is the only coloured thing on the wall.
    "noir": {
        "--overlay-text-color": "#ffffff",
        "--overlay-accent-color": "#b4b4b4",
        "--overlay-accent-text": "#101010",
        "--overlay-ambient-bg": "rgba(0, 0, 0, 0.52)",
        "--overlay-alert-bg": "rgba(0, 0, 0, 0.80)",
        "--overlay-surface-solid": "#1a1a1a",
        "--overlay-radius-pill": "999px",
        "--overlay-radius-card": "0.35rem",
        "--overlay-radius-panel": "0.45rem",
        "--overlay-radius-control": "0.3rem",
        "--overlay-radius-button": "0.3rem",
        "--overlay-radius-button-lg": "0.3rem",
        "--overlay-radius-group": "0.35rem",
        # The one theme left on a Helvetica clone, and deliberately: Helvetica is the
        # photo-caption face, which is exactly what a greyscale overlay over a
        # photograph is.
        "--overlay-font-family": _GROTESQUE,
        "--overlay-clock-weight": "200",
        "--overlay-sleep-color": "#8a8a8a",
    },
    # Ethan Schoonover's dark palette, which half this stylesheet was already quoting:
    # the stock weather watch (#b58900) and warning (#dc322f) are Solarized yellow and red.
    "solarized": {
        "--overlay-tint-rgb": "147, 161, 161",
        # base2 rather than Solarized's own base0/base1 body text. Those are chosen for
        # text on a base03 panel; this overlay's text sits over an arbitrary photo, and
        # at #93a1a1 the clock washed out against a bright one. The rest of the palette
        # is untouched -- this is the one place the source material assumes a background
        # that is not there.
        "--overlay-text-color": "#eee8d5",
        "--overlay-accent-color": "#2aa198",
        "--overlay-accent-text": "#002b36",
        # IBM's own face on a palette every developer recognises. It has enough
        # character to be identifiable without competing with the colours.
        "--overlay-font-family": _PLEX,
        "--overlay-ambient-bg": "rgba(0, 43, 54, 0.70)",
        "--overlay-alert-bg": "rgba(0, 43, 54, 0.90)",
        "--overlay-surface-solid": "#073642",
        "--overlay-positive": "#859900",
        "--overlay-negative": "#e0644f",
        "--overlay-caution": "#b58900",
        "--overlay-fault": "#cb4b16",
        "--overlay-severe": "#dc322f",
        "--overlay-watch": "#b58900",
        "--overlay-sleep-bg": "#002b36",
        "--overlay-sleep-color": "#8a3f3d",
    },
    # Amber phosphor. Square everything, kill the blur and the shadows, widen the
    # tracking and set it in a mono face -- the shape is doing as much of the work as
    # the colour. The status colours stay loud because a VT220 pastiche is not worth a
    # missed weather warning.
    "terminal": {
        "--overlay-tint-rgb": "255, 176, 0",
        "--overlay-text-color": "#ffb000",
        "--overlay-accent-color": "#ff8c00",
        "--overlay-accent-text": "#0a0800",
        "--overlay-ambient-bg": "rgba(8, 6, 0, 0.84)",
        "--overlay-alert-bg": "rgba(8, 6, 0, 0.93)",
        "--overlay-surface-solid": "#120d00",
        "--overlay-severity-text": "#0a0800",
        "--overlay-positive": "#2fbf2f",
        "--overlay-negative": "#ff4a3d",
        "--overlay-caution": "#ffb000",
        "--overlay-fault": "#ff7b00",
        "--overlay-severe": "#ff2d20",
        "--overlay-watch": "#ffcc00",
        "--overlay-radius-pill": "0",
        "--overlay-radius-round": "0",
        "--overlay-radius-panel": "0",
        "--overlay-radius-card": "0",
        "--overlay-radius-group": "0",
        "--overlay-radius-button-lg": "0",
        "--overlay-radius-control": "0",
        "--overlay-radius-button": "0",
        "--overlay-radius-sm": "0",
        "--overlay-radius-xs": "0",
        "--overlay-blur-badge": "0px",
        "--overlay-blur-card": "0px",
        "--overlay-blur-panel": "0px",
        "--overlay-shadow-card": "none",
        "--overlay-shadow-panel": "none",
        "--overlay-font-family": _MONO,
        # The one place a seven-segment face belongs: digits, a colon and AM/PM, at
        # 100px+, which is what the format was designed for. The date underneath goes
        # back to the mono face, because DSEG renders "Tuesday, September 29" as blocks.
        "--overlay-clock-font-family": _LCD,
        "--overlay-clock-date-font-family": _MONO,
        "--overlay-title-tracking": "0.22em",
        "--overlay-clock-weight": "400",
        "--overlay-clock-tracking": "0",
        "--overlay-sleep-clock-weight": "400",
        "--overlay-sleep-color": "#8a5f00",
    },
    # Cyan on near-black, condensed, tight radii and a heavier blur. Where terminal
    # removes depth, this leans into it.
    "neon": {
        "--overlay-tint-rgb": "180, 240, 255",
        "--overlay-text-color": "#e6fbff",
        "--overlay-accent-color": "#00e5ff",
        "--overlay-accent-text": "#00141c",
        "--overlay-ambient-bg": "rgba(4, 10, 20, 0.70)",
        "--overlay-alert-bg": "rgba(4, 10, 20, 0.88)",
        "--overlay-surface-solid": "#08131c",
        "--overlay-severity-text": "#00141c",
        "--overlay-positive": "#00ff9c",
        "--overlay-negative": "#ff2d6f",
        "--overlay-caution": "#ffc400",
        "--overlay-fault": "#ff7a1a",
        "--overlay-severe": "#ff1744",
        "--overlay-watch": "#ffd000",
        "--overlay-radius-panel": "0.3rem",
        "--overlay-radius-card": "0.25rem",
        "--overlay-radius-group": "0.25rem",
        "--overlay-radius-button-lg": "0.2rem",
        "--overlay-radius-control": "0.2rem",
        "--overlay-radius-button": "0.2rem",
        "--overlay-radius-sm": "0.15rem",
        "--overlay-blur-badge": "16px",
        "--overlay-blur-card": "20px",
        "--overlay-blur-panel": "24px",
        "--overlay-shadow-card": "0 0 0 1px rgba(0, 229, 255, 0.22), 0 0.5rem 2rem rgba(0, 0, 0, 0.55)",
        "--overlay-shadow-panel": "0 0 0 1px rgba(0, 229, 255, 0.22), 0 1.5rem 3rem rgba(0, 0, 0, 0.6)",
        "--overlay-font-family": _CONDENSED,  # IBM Plex Sans Condensed: tight and technical
        "--overlay-title-tracking": "0.16em",
        "--overlay-clock-weight": "200",
        "--overlay-sleep-color": "#0d6b7a",
    },
    # Magenta and violet, set in Quicksand. The look depends on the face as much as
    # the palette: rounded geometry reads as the era, where a Helvetica clone would
    # just read as a purple overlay.
    "synthwave": {
        "--overlay-tint-rgb": "255, 214, 255",
        "--overlay-text-color": "#f8e6ff",
        "--overlay-accent-color": "#ff4ecd",
        "--overlay-accent-text": "#1a062a",
        "--overlay-ambient-bg": "rgba(26, 6, 42, 0.74)",
        "--overlay-alert-bg": "rgba(26, 6, 42, 0.90)",
        "--overlay-surface-solid": "#241038",
        "--overlay-positive": "#3ef0b0",
        "--overlay-negative": "#ff5470",
        "--overlay-caution": "#ffb547",
        "--overlay-fault": "#ff7847",
        "--overlay-severe": "#ff2e63",
        "--overlay-watch": "#ffd166",
        # Quicksand's rounded geometry is closer to the era than a Century Gothic
        # clone: the look is Miami, not 1990s corporate.
        "--overlay-font-family": _GEOMETRIC_ROUND,
        "--overlay-title-tracking": "0.18em",
        "--overlay-clock-weight": "200",
        "--overlay-sleep-color": "#7a2f66",
    },
    # Warm end of the spectrum, set in Vollkorn. The one theme that reads as lamplight
    # rather than screen light, which is the point in a room used in the evening.
    "ember": {
        "--overlay-tint-rgb": "255, 233, 214",
        "--overlay-text-color": "#fff0e4",
        "--overlay-accent-color": "#ff8c42",
        "--overlay-accent-text": "#24110a",
        # A serif, and one of two themes that want one: Vollkorn has the weight and
        # warmth that Garamond deliberately does not.
        "--overlay-font-family": _SERIF_WARM,
        "--overlay-ambient-bg": "rgba(32, 16, 10, 0.66)",
        "--overlay-alert-bg": "rgba(32, 16, 10, 0.88)",
        "--overlay-surface-solid": "#2b1610",
        "--overlay-positive": "#8fbf5f",
        "--overlay-negative": "#ff6b5b",
        "--overlay-caution": "#ffb020",
        "--overlay-fault": "#e2622a",
        "--overlay-severe": "#d92d20",
        "--overlay-watch": "#ffb020",
        "--overlay-sleep-color": "#8a3a1a",
    },
    # Cool green and low chroma, set in EB Garamond. Sits well under photographs,
    # which is where this overlay spends most of its life.
    "forest": {
        "--overlay-tint-rgb": "222, 238, 226",
        "--overlay-text-color": "#eaf4ec",
        "--overlay-accent-color": "#7fc99a",
        "--overlay-accent-text": "#0c1a12",
        # Calm and organic, without Vollkorn's heft.
        "--overlay-font-family": _SERIF_CALM,
        "--overlay-ambient-bg": "rgba(12, 26, 18, 0.62)",
        "--overlay-alert-bg": "rgba(12, 26, 18, 0.86)",
        "--overlay-surface-solid": "#152418",
        "--overlay-positive": "#7fc99a",
        "--overlay-negative": "#e0705f",
        "--overlay-caution": "#e0b050",
        "--overlay-fault": "#d4803a",
        "--overlay-severe": "#c8402f",
        "--overlay-watch": "#e0b050",
        "--overlay-sleep-color": "#2f6b48",
    },
    # High contrast, no translucency, no blur. Not a style so much as an accessibility
    # setting: for a display that has to stay readable over a bright photo at a
    # distance, where every other theme trades some legibility for looking good.
    "contrast": {
        "--overlay-text-color": "#ffffff",
        "--overlay-accent-color": "#ffd400",
        "--overlay-accent-text": "#000000",
        # Heavy geometric. Weight and open counters are what survive being read from
        # across a room, which is this theme's entire job.
        "--overlay-font-family": _GEOMETRIC_HEAVY,
        "--overlay-ambient-bg": "rgba(0, 0, 0, 0.92)",
        "--overlay-alert-bg": "rgba(0, 0, 0, 0.97)",
        "--overlay-surface-solid": "#000000",
        "--overlay-offline-bg": "rgba(140, 0, 0, 0.97)",
        "--overlay-blur-badge": "0px",
        "--overlay-blur-card": "0px",
        "--overlay-blur-panel": "0px",
        "--overlay-border-width": "2px",
        "--overlay-clock-weight": "500",
        "--overlay-sleep-clock-weight": "400",
        "--overlay-sleep-color": "#c04040",
    },
}


def theme_names() -> list[str]:
    """Selectable theme names, with the default first and the rest alphabetical.

    The default leads because this list is what the on-screen picker and the Home
    Assistant select both render, and "put it back" should not require hunting.
    """
    rest = sorted(name for name in THEMES if name != DEFAULT_THEME)
    return [DEFAULT_THEME, *rest]


def theme_label(name: str) -> str:
    """Display form of a theme name, e.g. `solarized` -> `Solarized`."""
    return name.replace("-", " ").title()


def resolve_theme(name: str | None) -> dict[str, str]:
    """Token overrides for a theme name, or the default's for anything unrecognised.

    Unknown names fall back rather than raise: this value arrives from pulse.conf, from
    MQTT and from a tap on the display, and a kiosk showing the stock theme because of
    a typo is a nuisance where one showing a traceback is an outage.
    """
    return THEMES.get((name or "").strip().lower(), THEMES[DEFAULT_THEME])


def is_known_theme(name: str | None) -> bool:
    return (name or "").strip().lower() in THEMES


def normalize_theme(name: str | None) -> str:
    """The canonical theme name for a value from config, MQTT or the display."""
    candidate = (name or "").strip().lower()
    return candidate if candidate in THEMES else DEFAULT_THEME


def theme_font_stack(name: str | None) -> str:
    """The theme's own font stack, or "" when it is happy with the stylesheet's."""
    return resolve_theme(name).get("--overlay-font-family", "")


def theme_clock_font_stack(name: str | None) -> str:
    """The theme's clock stack, or "" when the clock should follow the overlay font."""
    return resolve_theme(name).get("--overlay-clock-font-family", "")
