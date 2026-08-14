"""Privy authentication for Euphoria.

Live API auth is cookie-based (verified from a logged-in Chrome request
replayed with Python urllib → 200 on users.getProfile). No Authorization
header is sent. The three cookies that matter:

    privy-id-token   identity JWT   ← this is the one that matters
    privy-token      access JWT
    privy-session    privy.euphoria.finance

Bearer identity_token returns 401 even when the JWT is valid. We still
use Privy's refresh endpoint to *mint* identity + access tokens, then
send them as those cookies — not as Authorization.

Capture the three cookies from a logged-in request (or Copy as cURL),
or capture a refresh token once (docs/AUTH.md). Do not scrape a browser.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from config import settings
from src.auth.cookies import (
    DEFAULT_PRIVY_SESSION,
    assemble_cookies,
    cookie_names_present,
    env_cookie_fields,
    format_cookie_header,
    load_cookie_fields,
    parse_cookie_header,
)
from src.utils import http as http_util

# Identity tokens are ~1h; refresh a bit early to avoid mid-trade expiry.
REFRESH_MARGIN_SECONDS = 300


class PrivyAuthError(RuntimeError):
    pass


@dataclass
class TokenBundle:
    identity_token: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0          # unix seconds for the identity token
    privy_user_id: str = ""
    cookie: str = ""                 # optional full Cookie header the user pasted

    @property
    def is_fresh(self) -> bool:
        return bool(self.identity_token) and time.time() < self.expires_at - REFRESH_MARGIN_SECONDS


def _jwt_payload(token: str) -> dict[str, Any]:
    """Read a JWT payload without verifying (we do not hold the key)."""
    import base64

    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _jwt_exp(token: str) -> float:
    try:
        return float(_jwt_payload(token).get("exp", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _jwt_sub(token: str) -> str:
    sub = _jwt_payload(token).get("sub")
    return str(sub).strip() if sub else ""


class PrivyAuth:
    """Holds cookies / tokens and refreshes the Privy bundle when possible."""

    def __init__(
        self,
        refresh_token: str | None = None,
        *,
        store_path: Path | None = None,
        session_path: Path | None = None,
        proxy: str | None = None,
    ) -> None:
        self.store_path = store_path or settings.TOKEN_STORE
        self.session_path = session_path or settings.SESSION_STORE
        self.proxy = proxy or settings.PROXY_URL
        self.tokens = self._load_store()
        self._ingest_files_and_env(refresh_token)
        if not self._has_any_credential():
            raise PrivyAuthError(
                "No Privy credentials. Capture the privy-id-token / privy-token / "
                "privy-session cookies from a logged-in request (EUPHORIA_COOKIE "
                "or EUPHORIA_PRIVY_ID_TOKEN), or set EUPHORIA_PRIVY_REFRESH_TOKEN "
                "to mint them. See docs/AUTH.md."
            )

    def _has_any_credential(self) -> bool:
        return bool(
            self.tokens.refresh_token
            or self.tokens.identity_token
            or self.tokens.access_token
            or parse_cookie_header(self.tokens.cookie).get("privy-id-token")
        )

    def _ingest_files_and_env(self, refresh_token: str | None) -> None:
        file_fields = load_cookie_fields(
            session_path=self.session_path, token_path=self.store_path
        )
        if not self.tokens.identity_token:
            self.tokens.identity_token = file_fields.get("privy-id-token", "")
        if not self.tokens.access_token:
            self.tokens.access_token = file_fields.get("privy-token", "")
        if not self.tokens.privy_user_id:
            self.tokens.privy_user_id = file_fields.get("privyUserId", "")
        env_refresh = refresh_token or os.environ.get("EUPHORIA_PRIVY_REFRESH_TOKEN", "")
        if env_refresh:
            self.tokens.refresh_token = env_refresh.strip()
        env_fields = env_cookie_fields()
        if env_fields.get("privy-id-token"):
            self.tokens.identity_token = env_fields["privy-id-token"]
            self.tokens.expires_at = _jwt_exp(self.tokens.identity_token) or time.time() + 3600
        if env_fields.get("privy-token"):
            self.tokens.access_token = env_fields["privy-token"]
        if env_fields.get("privyUserId"):
            self.tokens.privy_user_id = env_fields["privyUserId"]
        env_cookie = (os.environ.get("EUPHORIA_COOKIE") or "").strip()
        if env_cookie:
            self.tokens.cookie = env_cookie

    # -- persistence --------------------------------------------------------
    def _load_store(self) -> TokenBundle:
        try:
            data = json.loads(self.store_path.read_text())
            return TokenBundle(**{k: data[k] for k in TokenBundle.__annotations__ if k in data})
        except Exception:
            return TokenBundle()

    def _save_store(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self.store_path.write_text(json.dumps(asdict(self.tokens), indent=2))
            os.chmod(self.store_path, 0o600)
        except OSError:
            pass  # cache is an optimisation, never fatal

    # -- refresh ------------------------------------------------------------
    def _session_headers(self) -> dict[str, str]:
        return {
            "privy-app-id": settings.PRIVY_APP_ID,
            "privy-client": settings.PRIVY_CLIENT,
            "Origin": settings.ORIGIN,
            "Referer": settings.ORIGIN + "/",
            "Content-Type": "application/json",
        }

    def refresh(self) -> TokenBundle:
        """Exchange the refresh token for a new identity + access token pair."""
        if not self.tokens.refresh_token:
            raise PrivyAuthError(
                "Identity token expired and no refresh token is available. "
                "Capture EUPHORIA_PRIVY_REFRESH_TOKEN once (docs/AUTH.md) so the "
                "bot can renew itself, or paste fresh privy-* cookies."
            )
        url = f"{settings.PRIVY_AUTH_BASE}/sessions"
        with http_util.build_client(proxy=self.proxy, headers=self._session_headers()) as client:
            try:
                resp = http_util.request(
                    client, "POST", url,
                    json={"refresh_token": self.tokens.refresh_token},
                )
            except http_util.AuthError as exc:
                raise PrivyAuthError(
                    f"Privy rejected the refresh token ({exc}). It has likely been "
                    "revoked or rotated by a browser login -- capture a fresh one."
                ) from exc
        if resp.status_code >= 400:
            raise PrivyAuthError(f"Privy session refresh failed {resp.status_code}: {resp.text[:300]}")

        data: dict[str, Any] = resp.json()
        identity = data.get("identity_token") or data.get("identityToken") or ""
        access = data.get("token") or data.get("access_token") or ""
        new_refresh = data.get("refresh_token") or self.tokens.refresh_token
        if not identity:
            raise PrivyAuthError(
                "Privy session response contained no identity_token. "
                f"Keys returned: {sorted(data)}"
            )
        user_id = self.tokens.privy_user_id or _jwt_sub(identity)
        self.tokens = TokenBundle(
            identity_token=identity,
            access_token=access,
            refresh_token=new_refresh,
            expires_at=_jwt_exp(identity) or time.time() + 3600,
            privy_user_id=user_id,
            cookie=self.tokens.cookie,
        )
        self._save_store()
        return self.tokens

    # -- public API ---------------------------------------------------------
    def identity_token(self) -> str:
        """Return the identity JWT, refreshing from the refresh token if we can.

        Cookie-only sessions (no refresh token) are returned as-is even if the
        JWT looks expired — the API decides. We do not invent a token.
        """
        if self.tokens.refresh_token and not self.tokens.is_fresh:
            self.refresh()
        if self.tokens.identity_token:
            return self.tokens.identity_token
        raise PrivyAuthError(
            "No privy-id-token. Paste the cookies from a logged-in request "
            "(docs/AUTH.md) or set EUPHORIA_PRIVY_REFRESH_TOKEN."
        )

    def privy_user_id(self) -> str:
        env = (os.environ.get("EUPHORIA_PRIVY_USER_ID") or "").strip()
        if env:
            return env
        if self.tokens.privy_user_id:
            return self.tokens.privy_user_id
        return _jwt_sub(self.tokens.identity_token)

    def cookie_map(self) -> dict[str, str]:
        extra: dict[str, str] = {}
        file_fields = load_cookie_fields(
            session_path=self.session_path, token_path=self.store_path
        )
        extra.update({k: v for k, v in file_fields.items() if k != "privyUserId"})
        if self.tokens.cookie:
            extra.update(parse_cookie_header(self.tokens.cookie))
        extra.update({k: v for k, v in env_cookie_fields().items() if k != "privyUserId"})
        session = (
            (os.environ.get("EUPHORIA_PRIVY_SESSION") or "").strip()
            or extra.get("privy-session")
            or DEFAULT_PRIVY_SESSION
        )
        return assemble_cookies(
            identity_token=self.tokens.identity_token,
            access_token=self.tokens.access_token,
            session=session,
            extra=extra,
        )

    def cookie_header(self) -> str:
        return format_cookie_header(self.cookie_map())

    def auth_headers(self) -> dict[str, str]:
        """Cookie header only — Bearer 401s on the live API."""
        if self.tokens.refresh_token:
            self.identity_token()
        header = self.cookie_header()
        if "privy-id-token=" not in header:
            raise PrivyAuthError(
                "No privy-id-token cookie. Copy privy-id-token, privy-token and "
                "privy-session from a logged-in request (docs/AUTH.md)."
            )
        return {"Cookie": header}

    def cookie_status(self) -> list[tuple[str, int]]:
        """Present Privy cookie names and value lengths — never the values."""
        parsed = parse_cookie_header(self.cookie_header())
        return [(name, len(parsed[name])) for name in cookie_names_present(self.cookie_header())]
