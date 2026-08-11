"""SOCKS5 proxy discovery for the geo-block.

Rewritten to use httpx instead of shelling out to curl per candidate, and to
probe candidates concurrently so a working proxy is found in seconds rather
than minutes.
"""
from __future__ import annotations

import concurrent.futures
import time

import httpx

from config import settings

PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5"
    "&timeout=3000&country=all&ssl=all&anonymity=all"
)
CACHE_TTL = 600
_cache: tuple[float, list[str]] = (0.0, [])


def fetch_proxies(limit: int = 60) -> list[str]:
    """Fetch a SOCKS5 candidate list (cached for CACHE_TTL seconds)."""
    global _cache
    ts, cached = _cache
    if cached and time.time() - ts < CACHE_TTL:
        return cached[:limit]
    try:
        resp = httpx.get(PROXYSCRAPE_URL, timeout=15.0)
        resp.raise_for_status()
        proxies = [p.strip() for p in resp.text.splitlines() if p.strip() and ":" in p]
    except Exception:
        return cached[:limit]
    _cache = (time.time(), proxies)
    return proxies[:limit]


def probe(proxy: str, test_url: str = settings.ORIGIN, timeout: float = 8.0) -> str | None:
    """Return the proxy URL if it reaches Euphoria without a region block."""
    url = proxy if "://" in proxy else f"socks5h://{proxy}"
    try:
        with httpx.Client(
            proxy=url,
            timeout=timeout,
            headers={"User-Agent": settings.USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.get(test_url)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    if "Region Restricted" in resp.text or "403 Forbidden" in resp.text:
        return None
    return url


def find_working_proxy(max_candidates: int = 40, workers: int = 12) -> str | None:
    """Probe candidates concurrently; return the first that clears the geo-block."""
    if settings.PROXY_URL and probe(settings.PROXY_URL):
        return settings.PROXY_URL
    candidates = fetch_proxies(max_candidates)
    if not candidates:
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, c): c for c in candidates}
        for fut in concurrent.futures.as_completed(futures):
            result = fut.result()
            if result:
                for other in futures:
                    other.cancel()
                return result
    return None
