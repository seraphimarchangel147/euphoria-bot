"""Redstone oracle price monitor — Discord alerts for price moves."""
import asyncio
import json
import time
import subprocess

ALERT_THRESHOLD = 0.01  # 1% move
POLL_INTERVAL = 10       # seconds

_last_prices: dict[str, float] = {}


def fetch_price(symbol: str) -> float | None:
    """Fetch current price from Redstone oracle."""
    url = f"https://api.redstone.finance/prices?symbol={symbol}&provider=redstone"
    try:
        result = subprocess.run(
            ["curl", "-s", url, "--connect-timeout", "10"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        if data and len(data) > 0:
            return data[0].get("value")
    except Exception:
        pass
    return None


def check_alert(symbol: str, price: float) -> str | None:
    """Check if price moved enough to trigger an alert."""
    global _last_prices
    if symbol in _last_prices:
        change = (price - _last_prices[symbol]) / _last_prices[symbol]
        if abs(change) >= ALERT_THRESHOLD:
            direction = "📈" if change > 0 else "📉"
            _last_prices[symbol] = price
            return f"{direction} {symbol} {change:+.2%} → ${price:,.2f}"
    _last_prices[symbol] = price
    return None


async def monitor_prices(callback=None):
    """Continuously monitor prices and alert on significant moves."""
    print("Price monitor started...")
    while True:
        for symbol in ["ETH", "BTC"]:
            price = fetch_price(symbol)
            if price:
                alert = check_alert(symbol, price)
                if alert:
                    print(f"ALERT: {alert}")
                    if callback:
                        await callback(alert)
            else:
                print(f"WARNING: Failed to fetch {symbol} price")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(monitor_prices())
