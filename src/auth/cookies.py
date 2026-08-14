"""Assemble the Cookie header the live Euphoria API actually accepts.

Verified from a logged-in Chrome request (Copy as cURL, replayed with
Python urllib → 200 on users.getProfile):

* No Authorization header is sent.
* Auth is three cookies on api.mainnet.euphoria.finance:
  privy-token (access JWT), privy-id-token (identity JWT; this is the
  one that matters), privy-session=privy.euphoria.finance.
* Extra browser cookies (GCLB, analytics) are passed through only if the
  user pasted them; we never invent or scrape them.

Sources, env winning over files: EUPHORIA_COOKIE / EUPHORIA_PRIVY_ID_TOKEN
/ EUPHORIA_PRIVY_TOKEN / EUPHORIA_PRIVY_USER_ID, then tokens.json, then
session.json. Nothing here solves Turnstile or scrapes a browser.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from config import settings

DEFAULT_PRIVY_SESSION = "privy.euphoria.finance"
PRIVY_COOKIE_NAMES: tuple[str, ...] = ("privy-token", "privy-id-token", "privy-session")

# Map hand-written / env / file keys onto the live cookie names.
_ID_ALIASES = frozenset({
    "privy-id-token", "privy_id_token", "identity_token", "identityToken",
    "id_token", "idToken", "EUPHORIA_PRIVY_ID_TOKEN", "EUPHORIA_PRIVY_IDENTITY_TOKEN",
})
_ACCESS_ALIASES = frozenset({
    "privy-token", "privy_token", "access_token", "accessToken",
    "token", "EUPHORIA_PRIVY_TOKEN",
})
_SESSION_ALIASES = frozenset({
    "privy-session", "privy_session", "EUPHORIA_PRIVY_SESSION",
})
_USER_ALIASES = frozenset({
    "privyUserId", "privy_user_id", "privy-user-id", "EUPHORIA_PRIVY_USER_ID",
})
_HEADER_ALIASES = frozenset({"cookie", "Cookie", "EUPHORIA_COOKIE"})


def parse_cookie_header(header: str) -> dict[str, str]:
    """Parse a Cookie: header or Copy-as-cURL -b / -H 'Cookie: …' value."""
    out: dict[str, str] = {}
    text = (header or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        return out
    for part in text.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        key, _, val = piece.partition("=")
        key, val = key.strip(), val.strip()
        if key:
            out[key] = val
    return out


def format_cookie_header(cookies: Mapping[str, str]) -> str:
    """Stable Cookie header: the three Privy cookies first, then extras."""
    ordered: list[str] = []
    seen: set[str] = set()
    for name in PRIVY_COOKIE_NAMES:
        val = (cookies.get(name) or "").strip()
        if val:
            ordered.append(f"{name}={val}")
            seen.add(name)
    for key, val in cookies.items():
        if key in seen:
            continue
        val = (val or "").strip()
        if key and val:
            ordered.append(f"{key}={val}")
            seen.add(key)
    return "; ".join(ordered)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def fields_from_mapping(data: Mapping[str, Any]) -> dict[str, str]:
    """Extract cookie / privyUserId fields from a JSON object. No inventing."""
    cookies: dict[str, str] = {}
    user_id = ""
    for raw_key, raw_val in data.items():
        key = str(raw_key)
        val = _clean(raw_val)
        if not val:
            continue
        if key in _HEADER_ALIASES:
            cookies.update(parse_cookie_header(val))
        elif key in _ID_ALIASES:
            cookies["privy-id-token"] = val
        elif key in _ACCESS_ALIASES:
            cookies["privy-token"] = val
        elif key in _SESSION_ALIASES:
            cookies["privy-session"] = val
        elif key in _USER_ALIASES:
            user_id = val
    out = {k: v for k, v in cookies.items() if v}
    if user_id:
        out["privyUserId"] = user_id
    return out


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_cookie_fields(*, session_path: Path | None = None, token_path: Path | None = None) -> dict[str, str]:
    """Merge session.json then tokens.json. Caller overlays env."""
    session = Path(session_path) if session_path is not None else Path(
        os.environ.get("EUPHORIA_SESSION_STORE") or settings.SESSION_STORE
    )
    token = Path(token_path) if token_path is not None else Path(
        os.environ.get("EUPHORIA_TOKEN_STORE") or settings.TOKEN_STORE
    )
    merged: dict[str, str] = {}
    merged.update(fields_from_mapping(_read_json_object(session)))
    merged.update(fields_from_mapping(_read_json_object(token)))
    return merged


def env_cookie_fields() -> dict[str, str]:
    """Env-only overlay. Individual vars win over EUPHORIA_COOKIE."""
    out: dict[str, str] = {}
    raw = _clean(os.environ.get("EUPHORIA_COOKIE", ""))
    if raw:
        out.update(parse_cookie_header(raw))
    access = _clean(os.environ.get("EUPHORIA_PRIVY_TOKEN", ""))
    if access:
        out["privy-token"] = access
    identity = _clean(
        os.environ.get("EUPHORIA_PRIVY_ID_TOKEN")
        or os.environ.get("EUPHORIA_PRIVY_IDENTITY_TOKEN")
        or ""
    )
    if identity:
        out["privy-id-token"] = identity
    session = _clean(os.environ.get("EUPHORIA_PRIVY_SESSION", ""))
    if session:
        out["privy-session"] = session
    user_id = _clean(os.environ.get("EUPHORIA_PRIVY_USER_ID", ""))
    if user_id:
        out["privyUserId"] = user_id
    return out


def assemble_cookies(
    *,
    identity_token: str = "",
    access_token: str = "",
    session: str = "",
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the cookie map. Default privy-session if still missing."""
    cookies: dict[str, str] = {}
    if extra:
        cookies.update({k: v for k, v in extra.items() if k != "privyUserId" and _clean(v)})
    if _clean(access_token):
        cookies["privy-token"] = _clean(access_token)
    if _clean(identity_token):
        cookies["privy-id-token"] = _clean(identity_token)
    if _clean(session):
        cookies["privy-session"] = _clean(session)
    elif not _clean(cookies.get("privy-session", "")):
        cookies["privy-session"] = DEFAULT_PRIVY_SESSION
    return {k: v for k, v in cookies.items() if _clean(v)}


def cookie_names_present(header: str) -> list[str]:
    parsed = parse_cookie_header(header)
    return [name for name in PRIVY_COOKIE_NAMES if parsed.get(name)]
