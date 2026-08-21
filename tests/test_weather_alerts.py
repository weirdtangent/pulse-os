"""Tests for pulse.weather_alerts (NWS filtering, first-seen bookkeeping, stale fallback)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pulse.weather_alerts as wa
import pytest


def _iso(offset_minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=offset_minutes)).isoformat()


def _feature(
    alert_id: str,
    event: str,
    *,
    severity: str = "Severe",
    expires_in: int = 60,
    ends: str | None = None,
    description: str = "Radar indicated.",
    instruction: str = "Take cover.",
) -> dict:
    return {
        "properties": {
            "id": alert_id,
            "event": event,
            "severity": severity,
            "headline": f"{event} issued by NWS Test",
            "description": description,
            "instruction": instruction,
            "senderName": "NWS Test",
            "areaDesc": "Testville",
            "onset": _iso(-5),
            "ends": ends,
            "expires": _iso(expires_in),
        }
    }


class _Feed:
    """Stateful MockTransport handler: swap `features` between fetches, or force an error."""

    def __init__(self) -> None:
        self.features: list[dict] = []
        self.status = 200
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": self.features})


@pytest.fixture
def feed(monkeypatch) -> _Feed:
    handler = _Feed()
    real_client = httpx.Client  # capture before patching to avoid self-recursion
    monkeypatch.setattr(wa.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler), **k))
    return handler


def _client(**kwargs) -> wa.WeatherAlertClient:
    kwargs.setdefault("tiers", ("warning", "watch"))
    kwargs.setdefault("min_severity", "severe")
    return wa.WeatherAlertClient(37.2, -79.9, **kwargs)


# -- parsing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("warning,watch", ("warning", "watch")),
        ("WARNING, Advisory", ("warning", "advisory")),
        ("warning,warning", ("warning",)),
        # A typo must not silently open the floodgates to every statement.
        ("nonsense", wa.DEFAULT_TIERS),
        ("", wa.DEFAULT_TIERS),
        (None, wa.DEFAULT_TIERS),
    ],
)
def test_parse_tiers(spec, expected):
    assert wa.parse_tiers(spec) == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("extreme", "extreme"),
        ("MINOR", "minor"),
        ("any", "unknown"),
        ("all", "unknown"),
        ("garbage", wa.DEFAULT_MIN_SEVERITY),
        (None, wa.DEFAULT_MIN_SEVERITY),
    ],
)
def test_parse_min_severity(spec, expected):
    assert wa.parse_min_severity(spec) == expected


def test_parse_exclusions_normalizes_and_dedupes():
    assert wa.parse_exclusions(" Heat Advisory , heat advisory ,Air Quality Alert") == (
        "heat advisory",
        "air quality alert",
    )


@pytest.mark.parametrize(
    ("event", "tier"),
    [
        ("Tornado Warning", "warning"),
        ("Child Abduction Emergency", "warning"),
        ("Flood Watch", "watch"),
        ("Heat Advisory", "advisory"),
        ("Air Quality Alert", "advisory"),
        ("Special Weather Statement", "statement"),
        ("", "statement"),
    ],
)
def test_alert_tier(event, tier):
    assert wa.alert_tier(event) == tier


# -- fetching and filtering ---------------------------------------------------


def test_fetch_filters_by_tier_and_severity(feed):
    feed.features = [
        _feature("a", "Tornado Warning", severity="Extreme"),
        _feature("b", "Flood Watch", severity="Severe"),
        _feature("c", "Heat Advisory", severity="Moderate"),  # wrong tier
        _feature("d", "Winter Storm Warning", severity="Moderate"),  # right tier, too mild
    ]
    alerts = _client().fetch()
    assert [alert.id for alert in alerts] == ["a", "b"]


def test_unknown_severity_is_never_filtered_out(feed):
    """NWS sending no severity is a refusal to rate, not a low rating."""
    feed.features = [_feature("a", "Tornado Warning", severity="Unknown")]
    alerts = _client(min_severity="extreme").fetch()
    assert [alert.id for alert in alerts] == ["a"]
    assert alerts[0].severity == "unknown"


def test_exclusions_win_over_the_filters(feed):
    feed.features = [
        _feature("a", "Tornado Warning", severity="Extreme"),
        _feature("b", "Flood Warning", severity="Extreme"),
    ]
    alerts = _client(exclude=("flood warning",)).fetch()
    assert [alert.id for alert in alerts] == ["a"]


def test_alerts_sort_most_urgent_first(feed):
    feed.features = [
        _feature("watch", "Flood Watch", severity="Severe"),
        _feature("extreme", "Tornado Warning", severity="Extreme"),
        _feature("warning", "Severe Thunderstorm Warning", severity="Severe"),
    ]
    alerts = _client().fetch()
    assert [alert.id for alert in alerts] == ["extreme", "warning", "watch"]


def test_request_carries_point_status_and_contact(feed):
    _client(contact="ops@example.com").fetch()
    request = feed.requests[-1]
    assert request.url.params["point"] == "37.2000,-79.9000"
    assert request.url.params["status"] == "actual"
    # Cancellations are the absence of an alert; the active feed already omits them.
    assert request.url.params["message_type"] == "alert,update"
    assert "ops@example.com" in request.headers["User-Agent"]


def test_max_alerts_caps_the_list(feed):
    # Distinct event names, or the dedupe below would collapse them first.
    feed.features = [_feature(f"a{i}", f"Flood {i} Warning", severity="Extreme") for i in range(wa.MAX_ALERTS + 3)]
    assert len(_client().fetch()) == wa.MAX_ALERTS


# -- de-duplication -----------------------------------------------------------


def test_repeats_of_one_product_collapse_to_a_single_alert(feed):
    """A point can sit under four Small Craft Advisories for four adjacent zones."""
    feed.features = [_feature(f"z{i}", "Flood Warning", severity="Severe") for i in range(4)]
    alerts = _client().fetch()
    assert [alert.event for alert in alerts] == ["Flood Warning"]


def test_dedupe_keeps_the_most_severe_of_the_group(feed):
    feed.features = [
        _feature("mild", "Flood Warning", severity="Moderate"),
        _feature("bad", "Flood Warning", severity="Extreme"),
    ]
    assert [alert.id for alert in _client(min_severity="moderate").fetch()] == ["bad"]


def test_dedupe_keeps_the_longest_running_of_equal_severity(feed):
    """Collapsing must never shorten the window the display shows."""
    feed.features = [
        _feature("short", "Flood Warning", severity="Severe", ends=_iso(30)),
        _feature("long", "Flood Warning", severity="Severe", ends=_iso(600)),
    ]
    assert [alert.id for alert in _client().fetch()] == ["long"]


def test_dedupe_leaves_different_products_alone(feed):
    feed.features = [
        _feature("a", "Tornado Warning", severity="Extreme"),
        _feature("b", "Flood Warning", severity="Severe"),
    ]
    assert len(_client().fetch()) == 2


# -- first-seen bookkeeping ---------------------------------------------------


def test_first_seen_is_stable_across_polls(feed):
    """The banner window must not restart every time NWS reissues the same alert."""
    feed.features = [_feature("a", "Tornado Warning", severity="Extreme")]
    client = _client()
    first = client.fetch()[0].first_seen
    feed.features = [_feature("a", "Tornado Warning", severity="Extreme")]  # reissued, new expires
    assert client.fetch()[0].first_seen == first


def test_first_seen_resets_after_an_alert_clears(feed):
    """A recycled alert ID in a later storm deserves its own banner."""
    feed.features = [_feature("a", "Tornado Warning", severity="Extreme")]
    client = _client()
    first = client.fetch()[0].first_seen
    feed.features = []
    assert client.fetch() == []
    feed.features = [_feature("a", "Tornado Warning", severity="Extreme")]
    assert client.fetch()[0].first_seen != first


# -- failure handling ---------------------------------------------------------


def test_fetch_error_keeps_unexpired_alerts(feed):
    feed.features = [_feature("a", "Tornado Warning", severity="Extreme", expires_in=60)]
    client = _client()
    assert len(client.fetch()) == 1
    feed.status = 500
    assert [alert.id for alert in client.fetch()] == ["a"]


def test_fetch_error_drops_alerts_that_have_since_expired(feed):
    """Losing the uplink mid-storm must not pin a warning on screen past its expiry."""
    feed.features = [_feature("a", "Tornado Warning", severity="Extreme", expires_in=-1)]
    client = _client()
    assert len(client.fetch()) == 1
    feed.status = 500
    assert client.fetch() == []


def test_malformed_features_are_skipped_not_fatal(feed):
    feed.features = [
        "not a dict",  # type: ignore[list-item]
        {"properties": None},
        {"properties": {"id": "", "event": "Tornado Warning"}},  # no id
        _feature("a", "Tornado Warning", severity="Extreme"),
    ]
    assert [alert.id for alert in _client().fetch()] == ["a"]


# -- banner window ------------------------------------------------------------


def test_banner_active_within_window():
    alert = {"first_seen": 1000.0}
    assert wa.banner_active(alert, banner_minutes=15, now=1000.0 + 14 * 60)


def test_banner_active_expires_after_window():
    alert = {"first_seen": 1000.0}
    assert not wa.banner_active(alert, banner_minutes=15, now=1000.0 + 16 * 60)


def test_banner_disabled_by_zero_minutes():
    alert = {"first_seen": 1000.0}
    assert not wa.banner_active(alert, banner_minutes=0, now=1000.0)


@pytest.mark.parametrize("first_seen", [None, 0, "nope"])
def test_banner_inactive_without_a_usable_first_seen(first_seen):
    assert not wa.banner_active({"first_seen": first_seen}, banner_minutes=15, now=1000.0)
