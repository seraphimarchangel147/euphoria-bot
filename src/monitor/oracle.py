"""Redstone oracle price feed (httpx, with staleness checks)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from config import settings

MAX_PRICE_AGE_SECONDS = 120


class StalePriceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    timestamp: float          # unix seconds

    @property
    def age(self) -> float:
        return time.time() - self.timestamp


def fetch_quote(symbol: str, client: httpx.Client | None = None) -> Quote:
    """Fetch one symbol from Redstone. Raises on stale/absent data."""
    owns = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        resp = client.get(settings.REDSTONE_URL, params={"symbol": symbol, "provider": "redstone"})
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns:
            client.close()

    if not data:
        raise StalePriceError(f"Redstone returned no data for {symbol}")
    entry = data[0] if isinstance(data, list) else data
    price = entry.get("value")
    ts_ms = entry.get("timestamp", 0)
    if not price:
        raise StalePriceError(f"Redstone returned no price for {symbol}: {entry}")
    quote = Quote(symbol=symbol.upper(), price=float(price), timestamp=float(ts_ms) / 1000.0)
    if quote.age > MAX_PRICE_AGE_SECONDS:
        raise StalePriceError(f"{symbol} price is {quote.age:.0f}s old (max {MAX_PRICE_AGE_SECONDS}s)")
    return quote


def fetch_quotes(symbols: list[str]) -> dict[str, Quote]:
    """Fetch several symbols, skipping any that are stale/failed."""
    out: dict[str, Quote] = {}
    with httpx.Client(timeout=15.0) as client:
        for sym in symbols:
            try:
                out[sym.upper()] = fetch_quote(sym, client)
            except Exception:
                continue
    return out
