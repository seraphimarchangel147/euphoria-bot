"""Shared HTTP client: httpx + SOCKS5 proxy + retry/backoff.

Replaces the previous curl-subprocess approach. Real client objects mean we get
connection reuse, cookie jars, timeouts and typed errors instead of parsing
stdout.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

import httpx

from config import settings

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
RETRY_STATUS = {429, 500, 502, 503, 504}


class GeoBlockedError(RuntimeError):
    """Raised when Euphoria returns its region-restriction page."""


class AuthError(RuntimeError):
    """Raised on 401/403 that is *not* a geo-block."""


def _is_geoblock(resp: httpx.Response) -> bool:
    if resp.status_code not in (403, 451):
        return False
    body = resp.text[:2000]
    return ("Region Restricted" in body or "403 Forbidden" in body or "geo" in body.lower())


def build_client(
    *,
    proxy: str | None = None,
    base_url: str = "",
    headers: Mapping[str, str] | None = None,
) -> httpx.Client:
    """Create a configured httpx.Client. `proxy` accepts socks5h:// or http://."""
    hdrs = {
        "User-Agent": settings.USER_AGENT,
        "Origin": settings.ORIGIN,
        "Referer": settings.ORIGIN + "/",
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        hdrs.update(headers)
    return httpx.Client(
        base_url=base_url,
        headers=hdrs,
        timeout=DEFAULT_TIMEOUT,
        proxy=proxy or settings.PROXY_URL,
        follow_redirects=True,
    )


def request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 1.5,
    **kwargs: Any,
) -> httpx.Response:
    """Issue a request with retry/backoff on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:      # DNS, proxy, connect, read
            last_exc = exc
            time.sleep(backoff ** attempt)
            continue
        if _is_geoblock(resp):
            raise GeoBlockedError(
                f"{url} returned {resp.status_code} region-restricted. "
                "Set EUPHORIA_PROXY to a non-US SOCKS5 endpoint."
            )
        if resp.status_code in (401, 403):
            raise AuthError(f"{resp.status_code} for {url}: {resp.text[:200]}")
        if resp.status_code in RETRY_STATUS and attempt < retries - 1:
            time.sleep(backoff ** attempt)
            continue
        return resp
    raise last_exc if last_exc else RuntimeError(f"request to {url} failed")


def get_json(client: httpx.Client, url: str, **kwargs: Any) -> Any:
    resp = request(client, "GET", url, **kwargs)
    resp.raise_for_status()
    return resp.json()


def post_json(client: httpx.Client, url: str, payload: Any, **kwargs: Any) -> Any:
    resp = request(client, "POST", url, json=payload, **kwargs)
    resp.raise_for_status()
    return resp.json()
