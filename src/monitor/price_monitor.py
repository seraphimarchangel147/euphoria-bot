"""Price monitor with Discord webhook alerts."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from src.monitor import oracle

log = logging.getLogger("euphoria.monitor")

ALERT_THRESHOLD = float(os.environ.get("EUPHORIA_ALERT_THRESHOLD", "0.01"))
POLL_INTERVAL = int(os.environ.get("EUPHORIA_POLL_INTERVAL", "10"))
DISCORD_WEBHOOK = os.environ.get("EUPHORIA_DISCORD_WEBHOOK", "")
SYMBOLS = [s.strip().upper() for s in os.environ.get("EUPHORIA_SYMBOLS", "ETH,BTC").split(",") if s.strip()]

_last: dict[str, float] = {}


def check_alert(symbol: str, price: float, threshold: float = ALERT_THRESHOLD) -> str | None:
    """Return an alert string if the move since the last reference exceeds threshold."""
    prev = _last.get(symbol)
    if prev is None:
        _last[symbol] = price
        return None
    change = (price - prev) / prev
    if abs(change) >= threshold:
        _last[symbol] = price
        arrow = "UP" if change > 0 else "DOWN"
        return f"{arrow} {symbol} {change:+.2%} -> ${price:,.2f}"
    return None


async def send_discord(message: str) -> None:
    if not DISCORD_WEBHOOK:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(DISCORD_WEBHOOK, json={"content": message})
    except Exception as exc:
        log.warning("discord webhook failed: %s", exc)


async def monitor_prices(callback=None, iterations: int | None = None) -> None:
    """Poll the oracle and alert on significant moves. `iterations` bounds the loop for tests."""
    log.info("price monitor started: %s (threshold %.2f%%)", ", ".join(SYMBOLS), ALERT_THRESHOLD * 100)
    count = 0
    while iterations is None or count < iterations:
        quotes = await asyncio.to_thread(oracle.fetch_quotes, SYMBOLS)
        for symbol, quote in quotes.items():
            alert = check_alert(symbol, quote.price)
            if alert:
                log.info("ALERT %s", alert)
                await send_discord(alert)
                if callback:
                    await callback(alert)
        missing = set(SYMBOLS) - set(quotes)
        if missing:
            log.warning("no fresh price for: %s", ", ".join(sorted(missing)))
        count += 1
        if iterations is None or count < iterations:
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(monitor_prices())
