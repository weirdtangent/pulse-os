"""National Weather Service alert fetcher for the overlay's alert pill and banner.

Source: the NWS public API (https://api.weather.gov/alerts/active?point=lat,lon). It is
free, needs no API key, and answers a single GET with every alert whose polygon covers
the kiosk's coordinates. The one requirement is a descriptive User-Agent identifying the
caller, which NWS asks for so they can contact heavy users; see PULSE_WEATHER_ALERTS_CONTACT.

US-only. There is no worldwide equivalent with this coverage — Open-Meteo (which powers
the assistant's forecast in pulse/assistant/info_sources.py) has no alerts product at all,
and Europe's equivalent is MeteoAlarm's CAP feeds, a different shape entirely. A non-US
location simply yields no alerts rather than an error.

Filtering runs on two axes because NWS's own two axes disagree often enough to need both:
- tier, derived from the event name's last word (Warning / Watch / Advisory / Statement),
  which is how people actually talk about alerts, and
- severity, NWS's Extreme..Minor scale, which is what distinguishes a Heat Advisory from a
  Winter Storm Warning when both are nominally "Moderate".

Runs synchronously in a background daemon thread (see bin/kiosk-mqtt-listener.py),
mirroring the httpx style used by pulse/stock_ticker.py.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

LOGGER = logging.getLogger("pulse.weather_alerts")

ALERTS_URL = "https://api.weather.gov/alerts/active"
# NWS asks API clients to identify themselves and provide a contact; the repo URL is a
# reasonable default, and PULSE_WEATHER_ALERTS_CONTACT lets an operator supply their own.
DEFAULT_CONTACT = "https://github.com/weirdtangent/pulse-os"

# Ascending urgency. A "statement" is informational (Special Weather Statement), an
# "advisory" is inconvenient-but-survivable, a "watch" means conditions are favorable, and
# a "warning" means it is happening or imminent.
TIER_ORDER = ("statement", "advisory", "watch", "warning")
TIER_RANK = {tier: index for index, tier in enumerate(TIER_ORDER)}
DEFAULT_TIERS = ("warning", "watch")

# NWS CAP severity, ascending. "unknown" is what the feed sends when it declines to say.
SEVERITY_ORDER = ("unknown", "minor", "moderate", "severe", "extreme")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}
DEFAULT_MIN_SEVERITY = "severe"

# Sentinel for PULSE_WEATHER_ALERTS_BANNER_MINUTES="always" — the banner stays up for as
# long as the alert is active, and the pill never takes over.
BANNER_ALWAYS = -1

# Cap on how many alerts the pill/banner will carry. Coastal and mountain points routinely
# sit under half a dozen simultaneous products; the card lists them all, this only bounds
# what the state manager has to diff and render.
MAX_ALERTS = 8


def parse_tiers(spec: str | None) -> tuple[str, ...]:
    """Parse a comma-separated tier list ("warning,watch") into canonical tiers.

    Unknown tokens are dropped. An empty or fully-unrecognized spec falls back to the
    default (warnings and watches) rather than to "everything", because the failure mode
    of a typo should be fewer alerts on the wall, not every Special Weather Statement.
    """
    if not spec:
        return DEFAULT_TIERS
    tokens = [token.strip().lower() for token in spec.split(",")]
    selected = tuple(dict.fromkeys(token for token in tokens if token in TIER_RANK))
    return selected or DEFAULT_TIERS


def parse_min_severity(spec: str | None) -> str:
    """Parse the minimum-severity floor: a severity name, or "any"/"all" for no floor.

    An unrecognized value falls back to the default floor rather than to "no floor", for
    the same reason parse_tiers does: the failure mode of a typo should be fewer alerts on
    the wall, not every Minor advisory in the county.
    """
    normalized = (spec or "").strip().lower()
    if normalized in SEVERITY_RANK:
        return normalized
    if normalized in {"any", "all"}:
        return "unknown"
    return DEFAULT_MIN_SEVERITY


def parse_exclusions(spec: str | None) -> tuple[str, ...]:
    """Parse a comma-separated blocklist of NWS event names ("Heat Advisory,Air Quality Alert")."""
    if not spec:
        return ()
    return tuple(dict.fromkeys(token.strip().lower() for token in spec.split(",") if token.strip()))


def alert_tier(event: str) -> str:
    """Classify an NWS event name into a tier by its final word.

    NWS event names are rigorously suffixed, which makes this far more reliable than it
    looks: "Tornado Warning", "Flood Watch", "Heat Advisory", "Special Weather Statement".
    Two suffixes get folded in deliberately: "Emergency" (Child Abduction Emergency, Civil
    Danger Emergency) is at least as urgent as a warning, and "Alert" (Air Quality Alert)
    behaves like an advisory.
    """
    normalized = (event or "").strip().lower()
    if normalized.endswith(("warning", "emergency")):
        return "warning"
    if normalized.endswith("watch"):
        return "watch"
    if normalized.endswith(("advisory", "alert")):
        return "advisory"
    return "statement"


@dataclass(frozen=True)
class WeatherAlert:
    """One active NWS alert, reduced to what the overlay renders."""

    id: str
    event: str
    tier: str
    severity: str
    headline: str
    description: str
    instruction: str
    sender: str
    area: str
    onset: str
    ends: str
    expires: str
    # Wall-clock epoch of the first poll that saw this alert ID, NOT the NWS onset. The
    # banner window is measured from this so a kiosk that boots into a two-day-old winter
    # storm warning doesn't get a banner for an alert everyone already knows about.
    first_seen: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event": self.event,
            "tier": self.tier,
            "severity": self.severity,
            "headline": self.headline,
            "description": self.description,
            "instruction": self.instruction,
            "sender": self.sender,
            "area": self.area,
            "onset": self.onset,
            "ends": self.ends,
            "expires": self.expires,
            "first_seen": self.first_seen,
        }


def parse_banner_minutes(spec: str | None) -> int:
    """Parse the banner window: "always" (the default), "0" for never, or a minute count.

    Returned as an int so it can live in a frozen config, with BANNER_ALWAYS as the
    sentinel for "for as long as the alert is active".
    """
    normalized = (spec or "").strip().lower()
    if not normalized or normalized in {"always", "full", "-1"}:
        return BANNER_ALWAYS
    try:
        minutes = int(normalized)
    except ValueError:
        return BANNER_ALWAYS
    return BANNER_ALWAYS if minutes < 0 else minutes


def banner_active(alert: dict[str, Any], *, banner_minutes: int, now: float | None = None) -> bool:
    """Whether an alert should currently have a banner.

    Three modes, all through one setting:
    - BANNER_ALWAYS (the default): the banner is the display, for the alert's whole life.
      The strip turned out to be unobtrusive enough to leave up, and it carries the hazard
      text, which the pill has no room for.
    - 0: never; the pill alone carries the alert.
    - N > 0: a "this just happened" announcement for N minutes, then the pill takes over.

    In the timed mode the window is derived from first_seen rather than a UI flag, so it
    survives an overlay reload — a card refresh mid-storm cannot resurrect a banner that
    already expired. NWS issues a fresh alert ID when a watch is upgraded to a warning, so
    a genuine escalation gets a new window; routine "extended until 6 AM" reissues carry
    the same ID and do not.
    """
    if banner_minutes == BANNER_ALWAYS:
        return True
    if banner_minutes <= 0:
        return False
    try:
        first_seen = float(alert.get("first_seen") or 0.0)
    except (TypeError, ValueError):
        return False
    if first_seen <= 0:
        return False
    current = time.time() if now is None else now
    return (current - first_seen) < banner_minutes * 60


def _expiry_epoch(alert: WeatherAlert) -> float | None:
    """Epoch seconds for the alert's expires timestamp, or None if unparseable."""
    raw = (alert.expires or alert.ends or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class WeatherAlertClient:
    """Fetches active NWS alerts for one point, filtered to the tiers the room cares about."""

    def __init__(
        self,
        latitude: float,
        longitude: float,
        *,
        tiers: Sequence[str] = DEFAULT_TIERS,
        min_severity: str = DEFAULT_MIN_SEVERITY,
        exclude: Iterable[str] = (),
        contact: str | None = None,
        timeout: float = 10.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.tiers = tuple(tier for tier in tiers if tier in TIER_RANK) or DEFAULT_TIERS
        self.min_severity = min_severity if min_severity in SEVERITY_RANK else DEFAULT_MIN_SEVERITY
        self.exclude = frozenset(item.strip().lower() for item in exclude if item and item.strip())
        self.contact = (contact or "").strip() or DEFAULT_CONTACT
        self.timeout = timeout
        self._log = log or LOGGER.info
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        # alert id -> epoch of the first poll that saw it, so the banner window is stable
        # across polls and the pill can say "new" without re-deriving it from NWS fields.
        self._first_seen: dict[str, float] = {}
        self._cache: tuple[WeatherAlert, ...] = ()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        # Only the alerts thread calls fetch(), so a single reused client is safe and keeps
        # the TLS session warm across the (deliberately slow) poll cadence.
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={
                    "User-Agent": f"pulse-os ({self.contact})",
                    "Accept": "application/geo+json",
                },
                follow_redirects=True,
            )
        return self._client

    def fetch(self) -> list[WeatherAlert]:
        """Return the active, qualifying alerts for this point — never raising.

        On a fetch error the previous result is reused, but only for alerts whose expiry
        has not passed. That is the conservative choice in both directions: a network blip
        doesn't flap the pill off and back on, and a kiosk that loses its uplink mid-storm
        cannot keep insisting on a warning that has since lapsed.
        """
        try:
            payload = self._request()
        except httpx.HTTPError as exc:
            self._log(f"weather-alerts: fetch failed: {exc}")
            return self._stale_fallback()
        if payload is None:
            return self._stale_fallback()

        features = payload.get("features")
        if not isinstance(features, list):
            self._log("weather-alerts: unexpected payload (no features list)")
            return self._stale_fallback()

        now = time.time()
        seen_ids: set[str] = set()
        alerts: list[WeatherAlert] = []
        for feature in features:
            alert = self._build_alert(feature, now=now)
            if alert is None:
                continue
            if alert.id in seen_ids:
                continue
            seen_ids.add(alert.id)
            alerts.append(alert)

        # Drop first-seen bookkeeping for alerts that have fallen out of the active feed,
        # so a re-issued ID (NWS does recycle them across seasons) gets a fresh banner.
        with self._lock:
            self._first_seen = {key: value for key, value in self._first_seen.items() if key in seen_ids}

        alerts = _dedupe_by_event(alerts)
        alerts.sort(key=_sort_key)
        trimmed = alerts[:MAX_ALERTS]
        if len(alerts) > MAX_ALERTS:
            self._log(f"weather-alerts: {len(alerts)} active, showing the {MAX_ALERTS} most urgent")
        self._cache = tuple(trimmed)
        return trimmed

    def _stale_fallback(self) -> list[WeatherAlert]:
        now = time.time()
        kept = [alert for alert in self._cache if (_expiry_epoch(alert) or 0.0) > now]
        self._cache = tuple(kept)
        return kept

    def _request(self) -> dict[str, Any] | None:
        client = self._get_client()
        response = client.get(
            ALERTS_URL,
            params={
                "point": f"{self.latitude:.4f},{self.longitude:.4f}",
                # Exclude NWS's own test/exercise/draft traffic, and the "Cancel" message
                # type — a cancellation is the absence of an alert, and the active feed
                # already omits what it cancels.
                "status": "actual",
                "message_type": "alert,update",
            },
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    def _build_alert(self, feature: Any, *, now: float) -> WeatherAlert | None:
        if not isinstance(feature, dict):
            return None
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            return None
        alert_id = str(properties.get("id") or feature.get("id") or "").strip()
        event = str(properties.get("event") or "").strip()
        if not alert_id or not event:
            return None
        if event.lower() in self.exclude:
            return None
        tier = alert_tier(event)
        if tier not in self.tiers:
            return None
        severity = str(properties.get("severity") or "unknown").strip().lower()
        if severity not in SEVERITY_RANK:
            severity = "unknown"
        # "unknown" is not "below severe" — it is NWS declining to answer, and dropping
        # those would silently hide products that carry no severity at all. Only a stated
        # severity is held to the floor.
        if severity != "unknown" and SEVERITY_RANK[severity] < SEVERITY_RANK[self.min_severity]:
            return None
        with self._lock:
            first_seen = self._first_seen.setdefault(alert_id, now)
        return WeatherAlert(
            id=alert_id,
            event=event,
            tier=tier,
            severity=severity,
            headline=str(properties.get("headline") or "").strip(),
            description=str(properties.get("description") or "").strip(),
            instruction=str(properties.get("instruction") or "").strip(),
            sender=str(properties.get("senderName") or "").strip(),
            area=str(properties.get("areaDesc") or "").strip(),
            onset=str(properties.get("onset") or properties.get("effective") or "").strip(),
            ends=str(properties.get("ends") or "").strip(),
            expires=str(properties.get("expires") or "").strip(),
            first_seen=first_seen,
        )


def _dedupe_by_event(alerts: list[WeatherAlert]) -> list[WeatherAlert]:
    """Collapse repeats of the same product covering this point down to one.

    NWS issues one alert per zone, county, or river segment, so a single location routinely
    sits under several copies of the same product at once — four Small Craft Advisories for
    four adjacent marine zones, eight Flood Warnings for eight river gauges. Every alert in
    this list already covers the kiosk's coordinates, so same event name here means the same
    thing is happening to this room, and a display that says "Small Craft Advisory" four
    times is telling you one fact four times.

    Within a group the survivor is the highest severity, then one that is actually in
    effect, then the one that runs latest — so collapsing never shortens the window shown,
    downgrades what's on screen, or swaps in a segment that hasn't started. The full text of
    the survivor is kept intact; only the duplicate headers go away.
    """
    now = time.time()
    groups: dict[str, WeatherAlert] = {}
    for alert in alerts:
        key = alert.event.strip().lower()
        incumbent = groups.get(key)
        if incumbent is None or _dedupe_rank(alert, now=now) > _dedupe_rank(incumbent, now=now):
            groups[key] = alert
    return list(groups.values())


def _dedupe_rank(alert: WeatherAlert, *, now: float) -> tuple[int, int, float]:
    """Severity, then in-effect-now, then whichever runs latest.

    The middle term matters more than it looks. NWS reissues a long-running product as a
    series of time segments — the Aleutians carry four Small Craft Advisories for one
    stretch of water, one of which doesn't start until tomorrow morning. Ranking on end
    time alone would pick that future segment and put "until Sat 17:00" on the wall for
    weather that hasn't started. Anything already in effect outranks anything that hasn't.

    Uses `ends` ahead of `expires`, unlike _expiry_epoch: the question here is "which of
    these covers the most weather", not "has this bulletin lapsed".
    """
    onset = _parse_epoch(alert.onset)
    in_effect = 1 if onset is None or onset <= now else 0
    end = _parse_epoch(alert.ends) or _parse_epoch(alert.expires) or 0.0
    return (SEVERITY_RANK.get(alert.severity, 0), in_effect, end)


def _parse_epoch(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _sort_key(alert: WeatherAlert) -> tuple[int, int, float]:
    """Most urgent first: severity, then tier, then whichever arrived most recently."""
    return (
        -SEVERITY_RANK.get(alert.severity, 0),
        -TIER_RANK.get(alert.tier, 0),
        -alert.first_seen,
    )
