"""Public OHLC helpers for higher-timeframe bias.

Default source is Binance public klines (no API key). Tests inject a
``fetch_fn`` so they never hit the network.

This is bias context only — never a reason to chase far lottery squares.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.analytics.timeframes import TF_KEYS, Bar

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

# Binance interval for each TF key we expose.
INTERVAL_FOR_TF: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "1h": "1h",
    "4h": "4h",
    "D": "1d",
    "M": "1M",
}

SYMBOL_FOR_ASSET: dict[str, str] = {
    "ETH": "ETHUSDT",
    "BTC": "BTCUSDT",
}

DEFAULT_LIMIT = 24


def parse_klines(raw: Iterable[Any]) -> list[Bar]:
    """Parse Binance-style kline arrays into ``Bar``s."""
    bars: list[Bar] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            ts = int(row[0]) / 1000.0
            o = float(row[1])
            h = float(row[2])
            l = float(row[3])
            c = float(row[4])
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0:
            continue
        bars.append(Bar(ts=ts, open=o, high=h, low=l, close=c))
    return bars


def _default_fetch(url: str, timeout: float) -> Any:
    import json

    req = Request(url, headers={"User-Agent": "euphoria-bot-ohlc/1"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — public HTTPS
        return json.loads(resp.read().decode("utf-8"))


def fetch_klines(
    asset: str,
    tf_key: str,
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: float = 4.0,
    fetch_fn: Callable[[str, float], Any] | None = None,
) -> list[Bar]:
    """Fetch one timeframe of public klines. Empty on any failure."""
    symbol = SYMBOL_FOR_ASSET.get(asset.upper())
    interval = INTERVAL_FOR_TF.get(tf_key)
    if not symbol or not interval:
        return []
    url = (
        f"{BINANCE_KLINES}?symbol={symbol}&interval={interval}&limit={int(limit)}"
    )
    fetch = fetch_fn or _default_fetch
    try:
        raw = fetch(url, timeout)
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return parse_klines(raw)


def fetch_ohlc_stack(
    asset: str,
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: float = 4.0,
    fetch_fn: Callable[[str, float], Any] | None = None,
) -> dict[str, list[Bar]]:
    """Fetch every higher TF. Missing keys become empty lists."""
    out: dict[str, list[Bar]] = {}
    for key in TF_KEYS:
        out[key] = fetch_klines(
            asset, key, limit=limit, timeout=timeout, fetch_fn=fetch_fn
        )
    return out
