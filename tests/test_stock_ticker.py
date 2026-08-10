"""Tests for pulse.stock_ticker (quote fetch, fallback chain, and cache guarantees)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pulse.stock_ticker as st
import pytest


class _Router:
    """A stateful httpx.MockTransport handler routing Yahoo endpoints for tests.

    Toggle the flags between fetches to simulate provider outages and partial responses.
    """

    def __init__(self) -> None:
        self.crumb_ok = True
        self.quote_status = 200
        self.quote_symbols = {"^GSPC", "AAPL"}  # which symbols v7 returns
        self.chart_ok = True
        self.market_state = "REGULAR"
        self.post_price: float | None = None
        self.finnhub_status = 200
        self.finnhub_prices: dict[str, dict] = {}  # symbol -> {"c","d","dp"}
        self.finnhub_requests: list[httpx.Request] = []
        self.quote_time = 1_700_000_000  # what Yahoo stamps as regularMarketTime
        self.delayed_by = 0  # Yahoo exchangeDataDelayedBy, in minutes

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # Route on the parsed host/path rather than substring-matching the whole URL.
        host = request.url.host
        path = request.url.path
        if host == "finnhub.io":
            self.finnhub_requests.append(request)
            if self.finnhub_status != 200:
                return httpx.Response(self.finnhub_status, json={})
            symbol = request.url.params.get("symbol", "")
            data = self.finnhub_prices.get(symbol, {"c": 0, "d": None, "dp": None})
            return httpx.Response(200, json=data)
        if path.endswith("/v1/test/getcrumb"):
            return httpx.Response(200 if self.crumb_ok else 500, text="CRUMB" if self.crumb_ok else "")
        if host == "fc.yahoo.com":
            return httpx.Response(200, text="ok")
        if path.endswith("/v7/finance/quote"):
            if self.quote_status != 200:
                return httpx.Response(self.quote_status, json={})
            catalog = {
                "^GSPC": {
                    "symbol": "^GSPC",
                    "shortName": "S&P 500",
                    "regularMarketPrice": 100.0,
                    "regularMarketChange": 1.5,
                    "regularMarketChangePercent": 1.5,
                    "marketState": self.market_state,
                },
                "AAPL": {
                    "symbol": "AAPL",
                    "shortName": "Apple Inc.",
                    "regularMarketPrice": 200.0,
                    "regularMarketChange": -2.0,
                    "regularMarketChangePercent": -1.0,
                    "marketState": self.market_state,
                },
            }
            if self.post_price is not None:
                catalog["AAPL"]["postMarketPrice"] = self.post_price
            for entry in catalog.values():
                entry["regularMarketTime"] = self.quote_time
                entry["exchangeDataDelayedBy"] = self.delayed_by
            result = [catalog[s] for s in self.quote_symbols if s in catalog]
            return httpx.Response(200, json={"quoteResponse": {"result": result}})
        if path.startswith("/v8/finance/chart/"):
            if not self.chart_ok:
                return httpx.Response(500, json={})
            symbol = path.rsplit("/chart/", 1)[1]
            return httpx.Response(
                200,
                json={
                    "chart": {
                        "result": [
                            {
                                "meta": {
                                    "symbol": symbol,
                                    "shortName": symbol,
                                    "regularMarketPrice": 50.0,
                                    "chartPreviousClose": 49.0,
                                    "regularMarketTime": self.quote_time,
                                }
                            }
                        ]
                    }
                },
            )
        return httpx.Response(404)


@pytest.fixture
def router(monkeypatch) -> _Router:
    r = _Router()
    real_client = httpx.Client  # capture before patching to avoid self-recursion
    monkeypatch.setattr(st.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(r)))
    return r


def _ticker(symbols=("^SPX", "AAPL"), **kwargs) -> st.StockTicker:
    return st.StockTicker(list(symbols), **kwargs)


class TestParseSymbols:
    def test_parses_and_dedupes_and_uppercases(self):
        assert st.parse_symbols("^spx, aapl ; msft,AAPL") == ["^SPX", "AAPL", "MSFT"]

    def test_empty(self):
        assert st.parse_symbols("") == []
        assert st.parse_symbols(None) == []


class TestFetch:
    def test_v7_primary_success(self, router):
        ticker = _ticker()
        quotes = ticker.fetch()
        by_symbol = {q.symbol: q for q in quotes}
        assert set(by_symbol) == {"^SPX", "AAPL"}
        assert by_symbol["^SPX"].label == "S&P 500"  # friendly label, not ^GSPC
        assert by_symbol["^SPX"].price == 100.0 and by_symbol["^SPX"].is_up is True
        assert by_symbol["AAPL"].is_up is False
        ticker.close()

    def test_v7_401_triggers_crumb_refresh_and_retry(self, router):
        ticker = _ticker()
        calls = {"n": 0}
        real = ticker._yahoo_quote_request

        def flaky(client, crumb, symbols):  # 401 on first attempt, real data on retry
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real(client, crumb, symbols)

        ticker._yahoo_quote_request = flaky  # type: ignore[method-assign]
        quotes = ticker.fetch()
        assert calls["n"] == 2 and len(quotes) == 2
        ticker.close()

    def test_falls_back_to_v8_chart_when_v7_unavailable(self, router):
        router.crumb_ok = False  # no crumb -> v7 skipped entirely
        ticker = _ticker()
        quotes = ticker.fetch()
        assert {q.symbol for q in quotes} == {"^SPX", "AAPL"}
        # v8 chart values (price 50 vs prev close 49), no marketState
        assert all(q.price == 50.0 and q.market_state == "" for q in quotes)
        ticker.close()

    def test_all_providers_fail_returns_last_good_cache(self, router):
        ticker = _ticker()
        first = ticker.fetch()
        assert len(first) == 2
        router.crumb_ok = False
        router.chart_ok = False  # both Yahoo paths dead
        cached = ticker.fetch()
        assert [q.symbol for q in cached] == [q.symbol for q in first]
        assert [q.price for q in cached] == [q.price for q in first]
        ticker.close()

    def test_partial_fetch_keeps_missing_symbol_from_cache(self, router):
        ticker = _ticker()
        ticker.fetch()  # prime both symbols
        # Now v7 returns only ^GSPC, and v8 chart is down, so AAPL is absent this round.
        router.quote_symbols = {"^GSPC"}
        router.chart_ok = False
        quotes = ticker.fetch()
        by_symbol = {q.symbol: q for q in quotes}
        assert set(by_symbol) == {"^SPX", "AAPL"}  # AAPL retained from cache
        assert by_symbol["AAPL"].price == 200.0
        ticker.close()

    def test_afterhours_price_when_post_market(self, router):
        router.market_state = "POST"
        router.post_price = 205.0
        ticker = _ticker(afterhours=True)
        by_symbol = {q.symbol: q for q in ticker.fetch()}
        assert by_symbol["AAPL"].after_hours == 205.0
        assert by_symbol["AAPL"].as_dict()["after_hours"] == 205.0
        ticker.close()

    def test_afterhours_suppressed_when_disabled(self, router):
        router.market_state = "POST"
        router.post_price = 205.0
        ticker = _ticker(afterhours=False)
        by_symbol = {q.symbol: q for q in ticker.fetch()}
        assert by_symbol["AAPL"].after_hours is None
        ticker.close()

    def test_no_symbols_returns_empty(self, router):
        ticker = _ticker(symbols=())
        assert ticker.fetch() == []
        ticker.close()


class TestFinnhubProvider:
    def test_finnhub_prices_equities_yahoo_fills_indices(self, router):
        # Finnhub covers AAPL; it can't price the ^SPX index, which falls through to Yahoo.
        router.finnhub_prices = {"AAPL": {"c": 210.0, "d": 5.0, "dp": 2.44}}
        ticker = _ticker(symbols=("^SPX", "AAPL"), api_key="KEY")
        by_symbol = {q.symbol: q for q in ticker.fetch()}
        assert by_symbol["AAPL"].price == 210.0  # from Finnhub
        assert by_symbol["^SPX"].price == 100.0  # index from Yahoo v7
        ticker.close()

    def test_finnhub_bad_key_falls_back_to_yahoo(self, router):
        router.finnhub_status = 401
        ticker = _ticker(symbols=("AAPL",), api_key="BADKEY")
        by_symbol = {q.symbol: q for q in ticker.fetch()}
        assert by_symbol["AAPL"].price == 200.0  # Yahoo v7
        ticker.close()

    def test_no_key_skips_finnhub_entirely(self, router):
        # Even with a Finnhub price available, no key => provider not consulted.
        router.finnhub_prices = {"AAPL": {"c": 999.0, "d": 1.0, "dp": 1.0}}
        ticker = _ticker(symbols=("AAPL",))  # no api_key
        by_symbol = {q.symbol: q for q in ticker.fetch()}
        assert by_symbol["AAPL"].price == 200.0  # Yahoo, not the Finnhub 999
        assert router.finnhub_requests == []
        ticker.close()

    def test_api_key_sent_as_header_not_in_url(self, router):
        # The key must never appear in the query string (it can leak via error/log URLs).
        router.finnhub_prices = {"AAPL": {"c": 210.0, "d": 5.0, "dp": 2.44}}
        ticker = _ticker(symbols=("AAPL",), api_key="SECRETKEY")
        ticker.fetch()
        assert router.finnhub_requests, "expected a Finnhub request"
        req = router.finnhub_requests[0]
        assert "SECRETKEY" not in str(req.url)
        assert req.headers.get("X-Finnhub-Token") == "SECRETKEY"
        ticker.close()


class TestMarketHours:
    def test_weekend_is_closed(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        saturday = datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        assert st.is_us_market_hours(saturday) is False

    def test_weekday_midday_is_open(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        wednesday = datetime(2026, 8, 5, 11, 0, tzinfo=ZoneInfo("America/New_York"))
        assert st.is_us_market_hours(wednesday) is True

    def test_weekday_evening_is_closed(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        wednesday_evening = datetime(2026, 8, 5, 20, 0, tzinfo=ZoneInfo("America/New_York"))
        assert st.is_us_market_hours(wednesday_evening) is False


class TestMarketPhaseAndVisibility:
    _ET = ZoneInfo("America/New_York")
    _MIDDAY = datetime(2026, 8, 5, 11, 0, tzinfo=_ET)  # Wed, regular session
    _PRE = datetime(2026, 8, 5, 7, 0, tzinfo=_ET)
    _POST = datetime(2026, 8, 5, 18, 0, tzinfo=_ET)
    _SATURDAY = datetime(2026, 8, 8, 12, 0, tzinfo=_ET)

    def _q(self, symbol, state):
        return {"symbol": symbol, "market_state": state}

    # -- schedule fallback (no marketState on the quotes) --------------------

    def test_schedule_regular_when_no_state(self):
        assert st.us_market_phase([self._q("AAPL", "")], now=self._MIDDAY) == "regular"

    def test_schedule_pre_and_post(self):
        assert st.us_market_phase([], now=self._PRE) == "pre"
        assert st.us_market_phase([], now=self._POST) == "post"

    def test_schedule_weekend_closed(self):
        assert st.us_market_phase([], now=self._SATURDAY) == "closed"

    # -- exchange truth pulls the session toward closed ---------------------

    def test_marketstate_overrides_clock_on_holiday(self):
        # Clock says regular hours, but the exchange reports CLOSED (a holiday) -> closed.
        assert st.us_market_phase([self._q("AAPL", "CLOSED")], now=self._MIDDAY) == "closed"

    def test_marketstate_half_day_early_close(self):
        # Clock still reads regular at 11:00, but a half-day has flipped to POST -> post.
        assert st.us_market_phase([self._q("AAPL", "POST")], now=self._MIDDAY) == "post"

    def test_stale_open_state_cannot_reopen_closed_market(self):
        # StockTicker serves last-good cache (stale REGULAR) when a poll returns nothing;
        # on a weekend the clock must win so the bar stays hidden. Regression for the bar
        # remaining visible overnight/weekends on a stale marketState.
        stale = [self._q("AAPL", "REGULAR")]
        assert st.us_market_phase(stale, now=self._SATURDAY) == "closed"
        # ...and overnight on a weekday, too.
        overnight = datetime(2026, 8, 5, 2, 0, tzinfo=self._ET)
        assert st.us_market_phase(stale, now=overnight) == "closed"

    def test_most_open_us_quote_wins(self):
        quotes = [self._q("AAPL", "POST"), self._q("MSFT", "REGULAR")]
        assert st.us_market_phase(quotes, now=self._MIDDAY) == "regular"

    def test_foreign_index_state_ignored(self):
        # Nikkei trading (REGULAR) must not make the US ticker read as open.
        quotes = [self._q("^N225", "REGULAR"), self._q("AAPL", "CLOSED")]
        assert st.us_market_phase(quotes, now=self._MIDDAY) == "closed"

    def test_prepost_states_collapse(self):
        assert st.us_market_phase([self._q("AAPL", "PREPRE")], now=self._PRE) == "pre"
        assert st.us_market_phase([self._q("AAPL", "POSTPOST")], now=self._POST) == "post"

    # -- visibility rule per mode -------------------------------------------

    def test_visible_always(self):
        for phase in ("closed", "pre", "regular", "post"):
            assert st.ticker_visible("always", phase) is True

    def test_visible_market_regular_only(self):
        assert st.ticker_visible("market", "regular") is True
        for phase in ("closed", "pre", "post"):
            assert st.ticker_visible("market", phase) is False

    def test_visible_extended(self):
        for phase in ("pre", "regular", "post"):
            assert st.ticker_visible("extended", phase) is True
        assert st.ticker_visible("extended", "closed") is False


class TestQuoteFreshness:
    """Provider timestamps land on the quote, and go stale only when they should."""

    NOW = 1_700_000_000.0

    def _q(self, age_seconds):
        return {"symbol": "AAPL", "quote_time": self.NOW - age_seconds}

    # -- annotate_staleness ---------------------------------------------------

    def test_old_quote_flagged_during_regular_session(self):
        quotes = [self._q(600)]
        st.annotate_staleness(quotes, stale_after=300, phase="regular", now=self.NOW)
        assert quotes[0]["is_stale"] is True

    def test_fresh_quote_not_flagged(self):
        quotes = [self._q(30)]
        st.annotate_staleness(quotes, stale_after=300, phase="regular", now=self.NOW)
        assert "is_stale" not in quotes[0]

    def test_not_evaluated_outside_regular_session(self):
        # After the close every quote is legitimately hours old — flagging then would
        # light up the whole bar and mean nothing.
        for phase in ("pre", "post", "closed"):
            quotes = [self._q(99_999)]
            st.annotate_staleness(quotes, stale_after=300, phase=phase, now=self.NOW)
            assert "is_stale" not in quotes[0], phase

    def test_zero_threshold_disables(self):
        quotes = [self._q(99_999)]
        st.annotate_staleness(quotes, stale_after=0, phase="regular", now=self.NOW)
        assert "is_stale" not in quotes[0]

    def test_quote_without_timestamp_is_never_flagged(self):
        quotes = [{"symbol": "AAPL"}, {"symbol": "MSFT", "quote_time": 0}]
        st.annotate_staleness(quotes, stale_after=300, phase="regular", now=self.NOW)
        assert all("is_stale" not in q for q in quotes)

    # -- providers populate the timestamp ------------------------------------

    def test_yahoo_v7_carries_time_and_delay(self, router):
        router.quote_time = 1_699_999_000
        router.delayed_by = 15
        ticker = _ticker(("AAPL",))
        quote = ticker.fetch()[0]
        assert quote.quote_time == 1_699_999_000
        assert quote.delayed_by == 15
        assert quote.as_dict()["delayed_by"] == 15
        ticker.close()

    def test_realtime_feed_omits_delay_from_payload(self, router):
        router.delayed_by = 0
        ticker = _ticker(("AAPL",))
        payload = ticker.fetch()[0].as_dict()
        assert "delayed_by" not in payload  # nothing to say -> nothing rendered
        assert payload["quote_time"] == router.quote_time
        ticker.close()

    def test_finnhub_carries_trade_timestamp(self, router):
        router.finnhub_prices = {"AAPL": {"c": 210.0, "d": 1.0, "dp": 0.5, "t": 1_699_998_888}}
        ticker = _ticker(("AAPL",), api_key="k")
        quote = ticker.fetch()[0]
        assert quote.quote_time == 1_699_998_888
        assert quote.delayed_by == 0  # Finnhub has no delay field; treat as real-time
        ticker.close()

    def test_yahoo_v8_chart_carries_time(self, router):
        router.crumb_ok = False  # force the no-auth chart fallback
        router.quote_time = 1_699_997_777
        ticker = _ticker(("AAPL",))
        quote = ticker.fetch()[0]
        assert quote.quote_time == 1_699_997_777
        ticker.close()

    def test_cached_quote_goes_stale_when_provider_stops_answering(self, router):
        # The regression this whole feature exists for: fetch() serves last-good values
        # forever, so a dead provider must eventually show on the bar.
        ticker = _ticker(("AAPL",))
        first = ticker.fetch()
        assert first and "is_stale" not in first[0].as_dict()
        router.quote_status = 500
        router.chart_ok = False
        cached = [q.as_dict() for q in ticker.fetch()]
        st.annotate_staleness(cached, stale_after=300, phase="regular", now=router.quote_time + 3600)
        assert cached[0]["is_stale"] is True
        ticker.close()
