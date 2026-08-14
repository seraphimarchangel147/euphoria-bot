"""Typed client for the Euphoria tRPC API."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from config import settings
from src.auth.privy import PrivyAuth
from src.utils import http as http_util


class EuphoriaAPIError(RuntimeError):
    pass


@dataclass
class TradeRequest:
    """A trade before it is signed. Times are SECONDS; the payload converts to ms."""
    asset: str
    amount: float
    start_price: float
    price_interval: float
    time_interval_seconds: int = 5
    quote_multiplier: int = 5
    slippage_tolerance: float = 0.02
    cell_x: int = 0
    cell_y: int = 0
    start_time: int = field(default_factory=lambda: int(time.time()))


class EuphoriaAPI:
    """Wraps the tRPC procedures the frontend calls.

    tRPC v10 query convention: GET /<procedure>?input=<json-encoded>
    Mutations: POST /<procedure> with the raw JSON body as input.
    """

    def __init__(self, auth: PrivyAuth, proxy: str | None = None) -> None:
        self.auth = auth
        self.proxy = proxy or settings.PROXY_URL
        self._client = http_util.build_client(proxy=self.proxy, base_url=settings.API_BASE)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EuphoriaAPI":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- plumbing -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return self.auth.auth_headers()

    def query(self, procedure: str, payload: Any | None = None) -> Any:
        params = {"input": json.dumps(payload)} if payload is not None else None
        resp = http_util.request(
            self._client, "GET", f"/{procedure}", headers=self._headers(), params=params
        )
        return self._unwrap(procedure, resp)

    def mutate(self, procedure: str, payload: Any) -> Any:
        resp = http_util.request(
            self._client, "POST", f"/{procedure}", headers=self._headers(), json=payload
        )
        return self._unwrap(procedure, resp)

    @staticmethod
    def _unwrap(procedure: str, resp: Any) -> Any:
        if resp.status_code >= 400:
            raise EuphoriaAPIError(f"{procedure} -> {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise EuphoriaAPIError(f"{procedure} returned non-JSON: {resp.text[:200]}") from exc
        if isinstance(body, dict):
            if "error" in body:
                raise EuphoriaAPIError(f"{procedure} error: {body['error']}")
            # tRPC wraps successful results in result.data
            result = body.get("result")
            if isinstance(result, dict) and "data" in result:
                return result["data"]
        return body

    # -- procedures ---------------------------------------------------------
    def get_tier(self) -> Any:
        return self.query("users.getTier")

    def get_game_state(self) -> Any:
        return self.query("users.getGameState")

    @staticmethod
    def profile_input(privy_user_id: str) -> dict[str, str]:
        """tRPC input for users.getProfile — not null, not an empty object."""
        uid = (privy_user_id or "").strip()
        if not uid:
            raise EuphoriaAPIError(
                "users.getProfile needs privyUserId "
                "(EUPHORIA_PRIVY_USER_ID or the sub claim of privy-id-token)"
            )
        return {"privyUserId": uid}

    def get_profile(self, privy_user_id: str | None = None) -> Any:
        uid = (privy_user_id or "").strip() or self.auth.privy_user_id()
        return self.query("users.getProfile", self.profile_input(uid))

    def whoami(self) -> Any:
        """Authenticated probe: users.getProfile({privyUserId}). Not getTier."""
        return self.get_profile()

    @staticmethod
    def to_payload(req: TradeRequest, signature: str, nonce: int, **extra: Any) -> dict[str, Any]:
        """Build the executeTrade payload. timeInterval is milliseconds here."""
        payload: dict[str, Any] = {
            "asset": req.asset.upper(),
            "startTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(req.start_time)),
            "timeInterval": req.time_interval_seconds * 1000,
            "startPrice": req.start_price,
            "priceInterval": req.price_interval,
            "quote": req.quote_multiplier,
            "amount": req.amount,
            "slippageTolerance": req.slippage_tolerance,
            "nonce": nonce,
            "signature": signature,
            "cellX": req.cell_x,
            "cellY": req.cell_y,
        }
        payload.update(extra)
        return payload

    def execute_trade(self, payload: dict[str, Any]) -> Any:
        """Submit a fully-assembled trade.

        Requires botSignature / deviceFingerprint / approvalPermit alongside the
        EIP-712 signature; see docs/REVERSE_ENGINEERING.md. Missing pieces are
        reported up-front rather than as an opaque server rejection.
        """
        required = ("signature", "botSignature", "deviceFingerprint", "approvalPermit")
        missing = [k for k in required if not payload.get(k)]
        if missing:
            raise EuphoriaAPIError(
                "executeTrade payload is incomplete, missing: " + ", ".join(missing)
            )
        return self.mutate("trades.executeTrade", payload)
