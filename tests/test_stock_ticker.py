"""Tests for pulse.stock_ticker (quote fetch, fallback chain, and cache guarantees)."""

from __future__ import annotations

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
