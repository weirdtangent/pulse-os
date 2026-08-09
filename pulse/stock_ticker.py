"""Stock quote fetcher for the overlay ticker bar.

Fetches quotes for a configured list of symbols (major indices plus any equities
the user cares to watch) for display in the scrolling overlay ticker.

Provider chain (per symbol, first source that answers wins for that symbol):
- Optional: Finnhub (https://finnhub.io) when PULSE_TICKER_API_KEY is set. This is a
  licensed API with an explicit free tier for personal use, so it is the preferred
  source when configured. Free tier covers US equities/ETFs but generally not indices,
  which is fine — anything Finnhub can't price falls through to Yahoo below.
- Yahoo Finance v7 quote (one request for all remaining symbols; carries daily change,
  marketState, and after-hours price). Yahoo gates this behind a cookie + crumb, which
  this module obtains transparently and refreshes on expiry.
- Yahoo v8 chart, one request per symbol, needs no auth so it survives crumb-flow
  breakage (price vs. previous close; no marketState/after-hours).
- Last resort: the most recent successfully-fetched values, so the ticker never goes
  blank when the providers hiccup.

Data-source note: the Yahoo endpoints are unofficial/undocumented (Yahoo has no free
public API) and are used here the same way the yfinance library does — appropriate for
personal, non-commercial, low-volume home display, but not a licensed/ToS-sanctioned
use. Set PULSE_TICKER_API_KEY to prefer Finnhub, which does provide a licensed free
tier. (Stooq was evaluated but now gates its CSV endpoints behind a JavaScript anti-bot
challenge, so it is unusable headless and was dropped.)

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
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
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


# --- Ticker visibility by market session ------------------------------------
# The bar can be shown 24/7, only during the regular session, or through extended
# (pre/after) hours. The current session is taken from the exchange truth (Yahoo's
# per-symbol marketState) when available, so holidays and half-days are honored for
# free; when no quote carries a marketState (e.g. only Finnhub-priced equities, or the
# v8 chart fallback answered), we fall back to a plain ET clock schedule that can't see
# holidays — the same "just guess" tradeoff already used to pace the fetch cadence.

TICKER_HOURS_MODES = ("always", "market", "extended")

# Canonical Yahoo symbols for the non-US indices in _YAHOO_SYMBOLS. Their marketState
# reflects a foreign session, so they must not drive the US-session visibility decision.
_FOREIGN_INDEX_SYMBOLS = frozenset({"^FTSE", "^GDAXI", "^FCHI", "^STOXX50E", "^N225", "^HSI"})

# How "open" each Yahoo marketState is, so the most-open US quote wins (e.g. on a normal
# day one anchor may lag into POST while another still reads REGULAR — trust the latter).
_MARKET_STATE_RANK: dict[str, int] = {
    "REGULAR": 3,
    "PRE": 2,
    "PREPRE": 2,
    "POST": 1,
    "POSTPOST": 1,
    "CLOSED": 0,
}
# Yahoo's fine-grained states collapsed to the three sessions we care about.
_STATE_TO_PHASE: dict[str, str] = {
    "REGULAR": "regular",
    "PRE": "pre",
    "PREPRE": "pre",
    "POST": "post",
    "POSTPOST": "post",
    "CLOSED": "closed",
}


def _us_market_state(quotes: Sequence[dict[str, object]]) -> str | None:
    """Most-open marketState among US quotes, or None if none carry one.

    Foreign indices are skipped (their session is not the US session); plain equities and
    US indices are trusted to represent the US market on these providers.
    """
    best: str | None = None
    best_rank = -1
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").upper()
        canonical = _YAHOO_SYMBOLS.get(symbol, symbol).upper()
        if symbol in _FOREIGN_INDEX_SYMBOLS or canonical in _FOREIGN_INDEX_SYMBOLS:
            continue
        state = str(quote.get("market_state") or "").upper()
        rank = _MARKET_STATE_RANK.get(state)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best = state
    return best


def _schedule_phase(now: datetime | None = None) -> str:
    """US session from the ET clock alone: pre 04:00-09:30, regular 09:30-16:00,
    post 16:00-20:00, else closed. Weekends are closed. Ignores holidays."""
    current = now.astimezone(_MARKET_TZ) if now else datetime.now(_MARKET_TZ)
    if current.weekday() >= 5:  # Saturday/Sunday
        return "closed"
    minutes = current.hour * 60 + current.minute
    if 9 * 60 + 30 <= minutes <= 16 * 60:
        return "regular"
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre"
    if 16 * 60 < minutes <= 20 * 60:
        return "post"
    return "closed"


def us_market_phase(quotes: Sequence[dict[str, object]] | None = None, now: datetime | None = None) -> str:
    """Current US session — "regular", "pre", "post", or "closed".

    Prefers the exchange truth carried on the quotes (Yahoo marketState, which already
    accounts for holidays and half-days); falls back to a plain ET clock when no quote
    reports a state.
    """
    state = _us_market_state(quotes or ())
    if state is not None:
        return _STATE_TO_PHASE.get(state, "closed")
    return _schedule_phase(now)


def ticker_visible(mode: str, phase: str) -> bool:
    """Whether the ticker bar should be shown, given the configured hours mode."""
    if mode == "always":
        return True
    if mode == "extended":
        return phase in {"pre", "regular", "post"}
    return phase == "regular"  # "market" (default)


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
    """Fetches quotes via an optional Finnhub key, then Yahoo v7/v8, then last-good cache."""

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        afterhours: bool = True,
        api_key: str | None = None,
        timeout: float = 10.0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        self.afterhours = afterhours
        self.api_key = (api_key or "").strip() or None
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

    def _finnhub_symbol(self, canonical: str) -> str:
        # Finnhub uses plain tickers for US equities/ETFs; indices (with a caret) are
        # not on the free tier and simply return no data, so they fall through to Yahoo.
        return canonical

    def _label(self, canonical: str, fallback: str | None) -> str:
        return _LABELS.get(canonical) or (fallback or "").strip() or canonical.lstrip("^")

    def _provider_chain(self) -> list[Callable[[list[str]], list[TickerQuote] | None]]:
        chain: list[Callable[[list[str]], list[TickerQuote] | None]] = []
        if self.api_key:
            chain.append(self._fetch_finnhub)
        chain.append(self._fetch_yahoo_quote)
        chain.append(self._fetch_yahoo_chart)
        return chain

    # -- public API ------------------------------------------------------------

    def fetch(self) -> list[TickerQuote]:
        """Return the freshest quotes available, never raising and never blank-on-hiccup.

        Each provider is asked only for the symbols still uncovered, so (e.g.) Finnhub
        can price the equities while Yahoo fills in the indices it doesn't support. The
        result is then merged over the last-good cache per symbol, so a symbol a round
        happens to miss keeps its previous value instead of vanishing from the ticker.
        """
        if not self.symbols:
            return []
        collected: dict[str, TickerQuote] = {}
        remaining = list(self.symbols)
        for provider in self._provider_chain():
            if not remaining:
                break
            try:
                found = provider(remaining) or []
            except Exception as exc:  # nosec B112 - a bad provider must not stop the chain
                self._log(f"ticker: provider {provider.__name__} errored ({exc})")
                found = []
            for quote in found:
                collected[quote.symbol] = quote
            remaining = [symbol for symbol in self.symbols if symbol not in collected]
        with self._lock:
            if collected:
                merged = {quote.symbol: quote for quote in self._cache}
                merged.update(collected)
                # Keep only currently-configured symbols, in configured order.
                self._cache = tuple(merged[symbol] for symbol in self.symbols if symbol in merged)
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
                # Best-effort seed; the crumb request below sets cookies on its own too.
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
    # Each provider takes the list of still-uncovered symbols and returns quotes for
    # whatever it can price (or None). fetch() merges results across providers per symbol.

    def _fetch_finnhub(self, symbols: list[str]) -> list[TickerQuote] | None:
        """Optional licensed provider: Finnhub /quote, one request per symbol.

        The API key is sent via the X-Finnhub-Token header (never the query string), so it
        can't leak into logs through an httpx error message that echoes the request URL.
        Note: the free /quote endpoint has no after-hours field, so Finnhub-priced symbols
        carry no `AH` value — after-hours display is a Yahoo-only enrichment (documented).
        """
        if not self.api_key:
            return None
        client = self._get_client()
        headers = {"X-Finnhub-Token": self.api_key}
        quotes: list[TickerQuote] = []
        for canonical in symbols:
            try:
                response = client.get(
                    FINNHUB_QUOTE_URL,
                    params={"symbol": self._finnhub_symbol(canonical)},
                    headers=headers,
                )
                if response.status_code in (401, 403):
                    self._log("ticker: finnhub auth failed — check PULSE_TICKER_API_KEY")
                    return quotes or None
                if response.status_code == 429:
                    self._log("ticker: finnhub rate-limited; leaving rest to Yahoo")
                    return quotes or None
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                self._log(f"ticker: finnhub {canonical} failed ({_redact(str(exc), self.api_key)})")
                continue
            price = _coerce_float(data.get("c"))
            change = _coerce_float(data.get("d"))
            pct = _coerce_float(data.get("dp"))
            # Finnhub returns c=0 (and null d/dp) for symbols it can't price, e.g. indices
            # on the free tier — skip those so they fall through to Yahoo.
            if not price or price <= 0 or change is None or pct is None:
                continue
            quotes.append(
                TickerQuote(
                    symbol=canonical,
                    label=self._label(canonical, None),
                    price=price,
                    change=change,
                    change_pct=pct,
                    is_up=change >= 0,
                )
            )
        return quotes or None

    def _fetch_yahoo_quote(self, symbols: list[str]) -> list[TickerQuote] | None:
        """v7 quote (cookie + crumb), one request for all requested symbols."""
        client = self._get_client()
        crumb = self._ensure_crumb(client)
        if not crumb:
            return None
        payload = self._yahoo_quote_request(client, crumb, symbols)
        if payload is None:
            # A stale crumb comes back as 401; refresh once and retry.
            crumb = self._ensure_crumb(client, force=True)
            if not crumb:
                return None
            payload = self._yahoo_quote_request(client, crumb, symbols)
        if payload is None:
            return None
        results = (payload.get("quoteResponse") or {}).get("result") or []
        if not results:
            return None
        by_symbol = {str(item.get("symbol", "")).upper(): item for item in results}
        quotes: list[TickerQuote] = []
        for canonical in symbols:
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

    def _yahoo_quote_request(self, client: httpx.Client, crumb: str, symbols: list[str]) -> dict | None:
        yahoo_symbols = [self._yahoo_symbol(s) for s in symbols]
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

    def _fetch_yahoo_chart(self, symbols: list[str]) -> list[TickerQuote] | None:
        """Fallback: v8 chart, one request per symbol, no auth required."""
        client = self._get_client()
        quotes: list[TickerQuote] = []
        for canonical in symbols:
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


def _redact(message: str, secret: str | None) -> str:
    """Never let the API key reach the logs, even if some layer echoes it."""
    if secret and secret in message:
        return message.replace(secret, "***")
    return message


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    requested = parse_symbols(",".join(sys.argv[1:])) or ["^SPX", "^DJI", "^NDX"]
    ticker = StockTicker(requested, api_key=os.environ.get("PULSE_TICKER_API_KEY"))
    for quote in ticker.fetch():
        print(quote.as_dict())
    print(f"market_hours={is_us_market_hours()}")
    ticker.close()
