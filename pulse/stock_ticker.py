"""Stock quote fetcher for the overlay ticker bar.

Fetches quotes for a configured list of symbols (major indices plus any equities
the user cares to watch) for display in the scrolling overlay ticker.

Reliability model (chosen with the user):
- Primary: Yahoo Finance v7 quote (one request for all symbols; carries daily
  change, marketState, and after-hours price). Yahoo now gates this behind a
  cookie + crumb, which this module obtains transparently and refreshes on expiry.
- Fallback: Yahoo v8 chart, one request per symbol, needs no auth so it survives
  crumb-flow breakage (gives price vs. previous close; no marketState/after-hours).
  This is an independent request path from v7, so it also covers a v7 outage.
- Last resort: the most recent successfully-fetched values, so the ticker never
  goes blank when the providers hiccup.

(Stooq was evaluated as a non-Yahoo fallback but now puts its CSV endpoints behind
a JavaScript proof-of-work anti-bot challenge, so it is unusable from a headless
client and was dropped.)

Runs synchronously in a background daemon thread (see kiosk-mqtt-listener.py),
mirroring the httpx style used by pulse/assistant/info_sources.py.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

LOGGER = logging.getLogger("pulse.stock_ticker")

YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_COOKIE_SEED_URL = "https://fc.yahoo.com/"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Yahoo rejects the default httpx User-Agent, so present a browser-like one.
_USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) PulseOS-ticker/1.0"

_MARKET_TZ = ZoneInfo("America/New_York")

# Friendly (config) symbol -> Yahoo symbol, for indices whose tickers differ from what
# a user would naturally type. Plain equities (AAPL, MSFT, ...) pass through unchanged.
_YAHOO_SYMBOLS: dict[str, str] = {
    "^SPX": "^GSPC",  # S&P 500
    "^DJI": "^DJI",  # Dow Jones Industrial Average
    "^IXIC": "^IXIC",  # Nasdaq Composite
    "^NDX": "^NDX",  # Nasdaq 100
    "^RUT": "^RUT",  # Russell 2000
    "^VIX": "^VIX",  # Volatility index
    "^FTSE": "^FTSE",  # FTSE 100 (UK)
    "^GDAXI": "^GDAXI",  # DAX (Germany)
    "^FCHI": "^FCHI",  # CAC 40 (France)
    "^STOXX50E": "^STOXX50E",  # Euro Stoxx 50
    "^N225": "^N225",  # Nikkei 225 (Japan)
    "^HSI": "^HSI",  # Hang Seng (Hong Kong)
}

# Friendly display labels for common indices (fallback: provider short name or symbol).
_LABELS: dict[str, str] = {
    "^SPX": "S&P 500",
    "^GSPC": "S&P 500",
    "^DJI": "Dow",
    "^IXIC": "Nasdaq",
    "^NDX": "Nasdaq 100",
    "^RUT": "Russell 2000",
    "^VIX": "VIX",
    "^FTSE": "FTSE 100",
    "^GDAXI": "DAX",
    "^FCHI": "CAC 40",
    "^STOXX50E": "Euro Stoxx 50",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
}


def is_us_market_hours(now: datetime | None = None) -> bool:
    """Rough US equity regular-session check (Mon-Fri 09:30-16:00 ET, ignores holidays).

    Used to pace the fetch cadence; being wrong on a holiday only means an extra
    (harmless) fast poll, so exchange-holiday precision is not worth the dependency.
    """
    current = now.astimezone(_MARKET_TZ) if now else datetime.now(_MARKET_TZ)
    if current.weekday() >= 5:  # Saturday/Sunday
        return False
    minutes = current.hour * 60 + current.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


@dataclass(frozen=True)
class TickerQuote:
    """A single quote for the ticker bar."""

    symbol: str  # canonical symbol as configured
    label: str  # display label
    price: float
    change: float
    change_pct: float
    is_up: bool
    after_hours: float | None = None  # post-market price, when available
    market_state: str = ""  # REGULAR / PRE / POST / CLOSED / DELAYED / ...

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": self.symbol,
            "label": self.label,
            "price": round(self.price, 2),
            "change": round(self.change, 2),
            "change_pct": round(self.change_pct, 2),
            "is_up": self.is_up,
            "market_state": self.market_state,
        }
        if self.after_hours is not None:
            payload["after_hours"] = round(self.after_hours, 2)
        return payload


def parse_symbols(spec: str | None) -> list[str]:
    """Parse a comma/space/semicolon separated symbol list into canonical symbols."""
    if not spec:
        return []
    raw = spec.replace(";", ",").replace(" ", ",")
    seen: set[str] = set()
    symbols: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        canonical = token.upper()
        if canonical in seen:
            continue
        seen.add(canonical)
        symbols.append(canonical)
    return symbols


class StockTicker:
    """Fetches quotes with Yahoo v7 -> Yahoo v8 -> Stooq -> last-good-cache fallback."""

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        afterhours: bool = True,
        timeout: float = 10.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        self.afterhours = afterhours
        self.timeout = timeout
        self._log = log or LOGGER.info
        self._lock = threading.Lock()
        self._cache: tuple[TickerQuote, ...] = ()
        self._client: httpx.Client | None = None
        self._crumb: str | None = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- symbol mapping helpers ------------------------------------------------

    def _yahoo_symbol(self, canonical: str) -> str:
        return _YAHOO_SYMBOLS.get(canonical, canonical)

    def _label(self, canonical: str, fallback: str | None) -> str:
        return _LABELS.get(canonical) or (fallback or "").strip() or canonical.lstrip("^")

    # -- public API ------------------------------------------------------------

    def fetch(self) -> list[TickerQuote]:
        """Return the freshest quotes available, never raising and never blank-on-hiccup."""
        if not self.symbols:
            return []
        quotes = self._fetch_yahoo_quote() or self._fetch_yahoo_chart()
        if quotes:
            with self._lock:
                self._cache = tuple(quotes)
            return quotes
        with self._lock:
            return list(self._cache)

    # -- http client -----------------------------------------------------------

    def _get_client(self) -> httpx.Client:
        # A single reused client keeps the Yahoo cookie/crumb warm across polls.
        # Only the ticker thread calls fetch(), so no cross-thread client sharing.
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    def _ensure_crumb(self, client: httpx.Client, *, force: bool = False) -> str | None:
        if self._crumb and not force:
            return self._crumb
        try:
            # Seed the cookie jar (this URL commonly 404s but still sets the A1/A3 cookie).
            try:
                client.get(YAHOO_COOKIE_SEED_URL)
            except httpx.HTTPError:
                pass
            response = client.get(YAHOO_CRUMB_URL)
            crumb = response.text.strip()
            if response.status_code == 200 and crumb and "<" not in crumb:
                self._crumb = crumb
                return crumb
            self._log(f"ticker: yahoo crumb unavailable (status {response.status_code})")
        except httpx.HTTPError as exc:
            self._log(f"ticker: yahoo crumb fetch failed ({exc})")
        return None

    # -- providers -------------------------------------------------------------

    def _fetch_yahoo_quote(self) -> list[TickerQuote] | None:
        """Primary: v7 quote (cookie + crumb), one request for all symbols."""
        client = self._get_client()
        crumb = self._ensure_crumb(client)
        if not crumb:
            return None
        payload = self._yahoo_quote_request(client, crumb)
        if payload is None:
            # A stale crumb comes back as 401; refresh once and retry.
            crumb = self._ensure_crumb(client, force=True)
            if not crumb:
                return None
            payload = self._yahoo_quote_request(client, crumb)
        if payload is None:
            return None
        results = (payload.get("quoteResponse") or {}).get("result") or []
        if not results:
            return None
        by_symbol = {str(item.get("symbol", "")).upper(): item for item in results}
        quotes: list[TickerQuote] = []
        for canonical in self.symbols:
            item = by_symbol.get(self._yahoo_symbol(canonical).upper())
            if not item:
                continue
            price = item.get("regularMarketPrice")
            change = item.get("regularMarketChange")
            pct = item.get("regularMarketChangePercent")
            if price is None or change is None or pct is None:
                continue
            market_state = str(item.get("marketState") or "")
            after_hours = None
            if self.afterhours and market_state in {"POST", "POSTPOST"}:
                after_hours = _coerce_float(item.get("postMarketPrice"))
            try:
                quotes.append(
                    TickerQuote(
                        symbol=canonical,
                        label=self._label(canonical, item.get("shortName")),
                        price=float(price),
                        change=float(change),
                        change_pct=float(pct),
                        is_up=float(change) >= 0,
                        after_hours=after_hours,
                        market_state=market_state,
                    )
                )
            except (TypeError, ValueError):
                continue
        return quotes or None

    def _yahoo_quote_request(self, client: httpx.Client, crumb: str) -> dict | None:
        yahoo_symbols = [self._yahoo_symbol(s) for s in self.symbols]
        try:
            response = client.get(
                YAHOO_QUOTE_URL,
                params={"symbols": ",".join(yahoo_symbols), "crumb": crumb},
            )
            if response.status_code in (401, 403):
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._log(f"ticker: yahoo quote failed ({exc})")
            return None

    def _fetch_yahoo_chart(self) -> list[TickerQuote] | None:
        """Fallback: v8 chart, one request per symbol, no auth required."""
        client = self._get_client()
        quotes: list[TickerQuote] = []
        for canonical in self.symbols:
            meta = self._chart_meta(client, self._yahoo_symbol(canonical))
            if not meta:
                continue
            price = _coerce_float(meta.get("regularMarketPrice"))
            prev_close = _coerce_float(meta.get("chartPreviousClose"))
            if price is None or not prev_close:
                continue
            change = price - prev_close
            quotes.append(
                TickerQuote(
                    symbol=canonical,
                    label=self._label(canonical, meta.get("shortName")),
                    price=price,
                    change=change,
                    change_pct=change / prev_close * 100.0,
                    is_up=change >= 0,
                )
            )
        return quotes or None

    def _chart_meta(self, client: httpx.Client, yahoo_symbol: str) -> dict | None:
        try:
            response = client.get(
                f"{YAHOO_CHART_URL}{yahoo_symbol}",
                params={"interval": "1d", "range": "1d"},
            )
            response.raise_for_status()
            result = (response.json().get("chart") or {}).get("result") or []
            if not result:
                return None
            return result[0].get("meta") or None
        except (httpx.HTTPError, ValueError) as exc:
            self._log(f"ticker: yahoo chart {yahoo_symbol} failed ({exc})")
            return None


def _coerce_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    requested = parse_symbols(",".join(sys.argv[1:])) or ["^SPX", "^DJI", "^NDX"]
    ticker = StockTicker(requested)
    for quote in ticker.fetch():
        print(quote.as_dict())
    print(f"market_hours={is_us_market_hours()}")
    ticker.close()
