"""SOCKS5 proxy helper — auto-discovers non-US proxies to bypass geo-blocking."""
import subprocess
import json
import time

PROXY_SCRAPE_URL = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=3000&country=all&ssl=all&anonymity=all"

_cached_proxies: list[str] = []
_cache_time: float = 0
CACHE_TTL = 600  # 10 minutes


def fetch_proxies() -> list[str]:
    """Fetch fresh SOCKS5 proxy list from proxyscrape."""
    global _cached_proxies, _cache_time
    if _cached_proxies and time.time() - _cache_time < CACHE_TTL:
        return _cached_proxies

    try:
        result = subprocess.run(
            ["curl", "-s", PROXY_SCRAPE_URL, "--connect-timeout", "10"],
            capture_output=True, text=True, timeout=15,
        )
        proxies = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        _cached_proxies = proxies
        _cache_time = time.time()
        return proxies
    except Exception:
        return _cached_proxies


def find_working_proxy(test_url: str = "https://euphoria.finance", timeout: int = 10) -> str | None:
    """Find a SOCKS5 proxy that bypasses Euphoria's geo-block."""
    proxies = fetch_proxies()
    for proxy in proxies[:20]:
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--proxy", f"socks5h://{proxy}", test_url,
                 "-H", "User-Agent: Mozilla/5.0",
                 "--connect-timeout", str(timeout), "--max-time", str(timeout + 5)],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            code = result.stdout.strip()
            if code in ("200", "301", "302"):
                # Verify it's not the geo-block page
                check = subprocess.run(
                    ["curl", "-s", "--proxy", f"socks5h://{proxy}", test_url,
                     "-H", "User-Agent: Mozilla/5.0",
                     "--connect-timeout", str(timeout), "--max-time", str(timeout + 5)],
                    capture_output=True, text=True, timeout=timeout + 10,
                )
                if "Region Restricted" not in check.stdout:
                    return f"socks5h://{proxy}"
        except Exception:
            continue
    return None


def api_request(path: str, method: str = "GET", headers: dict = None,
                body: str = None, proxy: str = None) -> dict:
    """Make an API request through a proxy."""
    url = f"https://api.mainnet.euphoria.finance/{path}"
    cmd = ["curl", "-s", "--max-time", "15"]

    if proxy:
        cmd.extend(["--proxy", proxy])

    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])

    if method == "POST":
        cmd.extend(["-X", "POST"])
    if body:
        cmd.extend(["-d", body])

    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout, "error": "json_decode_failed"}
