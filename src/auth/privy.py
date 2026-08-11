"""Privy authentication for Euphoria.

The blocker this module solves
------------------------------
Euphoria's API authenticates with a Privy **identity token**, not the access
token that sits in localStorage. The identity token is short-lived and the
frontend keeps it in memory, which is why scraping localStorage failed.

But it does not have to be scraped at all. Privy issues BOTH tokens from the
session endpoint when given a **refresh token**:

    POST https://auth.privy.io/api/v1/sessions
    headers: privy-app-id, privy-client, Origin
    body:    {"refresh_token": "<refresh token>"}
    ->       {"token": <access>, "identity_token": <identity>,
              "refresh_token": <rotated refresh>, ...}

The refresh token is a *persistent* credential the browser stores in the
`privy-refresh-token` cookie / localStorage entry. Capture it once, hand it to
the bot, and the bot mints fresh identity tokens on its own indefinitely -- it
rotates and re-persists the refresh token on every call.

Capture instructions live in docs/AUTH.md.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import httpx

from config import settings
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

    @property
    def is_fresh(self) -> bool:
        return bool(self.identity_token) and time.time() < self.expires_at - REFRESH_MARGIN_SECONDS


def _jwt_exp(token: str) -> float:
    """Read `exp` out of a JWT payload without verifying (we do not hold the key)."""
    import base64

    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return float(payload.get("exp", 0))
    except Exception:
        return 0.0


class PrivyAuth:
    """Holds and refreshes the Privy token bundle."""

    def __init__(
        self,
        refresh_token: str | None = None,
        *,
        store_path: Path | None = None,
        proxy: str | None = None,
    ) -> None:
        self.store_path = store_path or settings.TOKEN_STORE
        self.proxy = proxy or settings.PROXY_URL
        self.tokens = self._load_store()
        env_refresh = refresh_token or os.environ.get("EUPHORIA_PRIVY_REFRESH_TOKEN", "")
        if env_refresh:
            self.tokens.refresh_token = env_refresh
        # An identity token can also be pasted directly for a one-off run.
        direct = os.environ.get("EUPHORIA_PRIVY_IDENTITY_TOKEN", "")
        if direct:
            self.tokens.identity_token = direct
            self.tokens.expires_at = _jwt_exp(direct) or time.time() + 3600
        if not (self.tokens.refresh_token or self.tokens.identity_token):
            raise PrivyAuthError(
                "No Privy credentials. Set EUPHORIA_PRIVY_REFRESH_TOKEN (preferred, "
                "self-renewing) or EUPHORIA_PRIVY_IDENTITY_TOKEN (expires in ~1h). "
                "See docs/AUTH.md for how to capture them."
            )

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
                "bot can renew itself without a browser."
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
        self.tokens = TokenBundle(
            identity_token=identity,
            access_token=access,
            refresh_token=new_refresh,
            expires_at=_jwt_exp(identity) or time.time() + 3600,
        )
        self._save_store()
        return self.tokens

    # -- public API ---------------------------------------------------------
    def identity_token(self) -> str:
        """Return a valid identity token, refreshing it if needed."""
        if not self.tokens.is_fresh:
            self.refresh()
        return self.tokens.identity_token

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.identity_token()}"}
