"""Live wallet balance, read straight off MegaETH.

Why on-chain rather than the API
--------------------------------
``users.getGameState`` needs a Privy identity token *and* a non-US IP, so it is
exactly the call that stops working when you most want a number. ``balanceOf``
needs neither: it is a plain ``eth_call`` against the USDM contract, it answers
from any IP, and it cannot disagree with the chain. The tRPC balance is kept as
a secondary reading for the in-game figure.

Finding the address
-------------------
In order of preference: an explicit env override, whatever the extension
scraped from the logged-in tab, the ``linked_accounts`` claim on a stored Privy
identity token, and finally the signing key if one is configured. Reading a JWT
claim is a local, read-only decode -- no signature check, because we are not
trusting it for anything but a public address.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from config import settings

# Standard ERC20 selectors; no keccak dependency needed for these four.
SEL_BALANCE_OF = "0x70a08231"
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ANY_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
DEFAULT_TIMEOUT = 12.0


class WalletError(RuntimeError):
    pass


@dataclass
class WalletSnapshot:
    address: str = ""
    usdm: float | None = None
    usdm_raw: int | None = None
    decimals: int = 18
    native: float | None = None
    source: str = ""
    ok: bool = False
    error: str = ""
    checked_at: float = 0.0
    game_state: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- address resolution ----------------------------------------------------
def _addresses_in_jwt(token: str) -> list[str]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return []
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return []
    return ANY_ADDRESS_RE.findall(json.dumps(claims))


def _from_token_store() -> str:
    try:
        data = json.loads(settings.TOKEN_STORE.read_text())
    except Exception:
        return ""
    direct = data.get("wallet_address") or data.get("walletAddress") or ""
    if isinstance(direct, str) and ADDRESS_RE.match(direct.strip()):
        return direct.strip()
    for key in ("identity_token", "access_token"):
        found = _addresses_in_jwt(data.get(key) or "")
        if found:
            return found[0]
    return ""


def _from_session_store() -> str:
    try:
        data = json.loads(settings.SESSION_STORE.read_text())
    except Exception:
        return ""
    for key in ("wallet_address", "walletAddress"):
        val = data.get(key)
        if isinstance(val, str) and ADDRESS_RE.match(val.strip()):
            return val.strip()
    return ""


def resolve_address(explicit: str | None = None) -> tuple[str, str]:
    """Return (address, where_it_came_from). Empty address if nothing is known."""
    if explicit and ADDRESS_RE.match(explicit.strip()):
        return explicit.strip(), "explicit"
    env = os.environ.get("EUPHORIA_WALLET_ADDRESS", "").strip()
    if ADDRESS_RE.match(env):
        return env, "env"
    from_session = _from_session_store()
    if from_session:
        return from_session, "extension"
    from_token = _from_token_store()
    if from_token:
        return from_token, "privy-token"
    if settings.PRIVATE_KEY:
        try:
            from src.trader import eip712

            return eip712.address_for_key(settings.PRIVATE_KEY), "private-key"
        except Exception:
            pass
    return "", "unknown"


# --- chain reads -----------------------------------------------------------
def _rpc(client: httpx.Client, method: str, params: list[Any], rpc_url: str) -> Any:
    resp = client.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, dict) and body.get("error"):
        raise WalletError(f"{method}: {body['error']}")
    return body.get("result") if isinstance(body, dict) else None


def _pad_address(address: str) -> str:
    return address.lower().replace("0x", "").rjust(64, "0")


def _hex_to_int(value: Any) -> int:
    if not isinstance(value, str) or not value.startswith("0x") or value == "0x":
        return 0
    return int(value, 16)


def read_wallet(
    address: str | None = None,
    *,
    token: str = settings.USDM_ADDRESS,
    rpc_url: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> WalletSnapshot:
    """Read the USDM and native balances for the operator's wallet."""
    resolved, source = resolve_address(address)
    snap = WalletSnapshot(address=resolved, source=source, checked_at=time.time())
    if not resolved:
        snap.error = (
            "no wallet address. Set EUPHORIA_WALLET_ADDRESS, or let the extension "
            "report one from the logged-in tab."
        )
        return snap

    rpc_url = rpc_url or settings.RPC_URL
    owns = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        call = {"to": token, "data": SEL_BALANCE_OF + _pad_address(resolved)}
        snap.usdm_raw = _hex_to_int(_rpc(client, "eth_call", [call, "latest"], rpc_url))
        try:
            dec = _hex_to_int(_rpc(client, "eth_call", [{"to": token, "data": SEL_DECIMALS}, "latest"], rpc_url))
            snap.decimals = dec if 0 < dec <= 36 else 18
        except Exception:
            snap.decimals = 18
        snap.usdm = snap.usdm_raw / (10 ** snap.decimals)
        try:
            wei = _hex_to_int(_rpc(client, "eth_getBalance", [resolved, "latest"], rpc_url))
            snap.native = wei / 1e18
        except Exception:
            snap.native = None
        snap.ok = True
    except (httpx.HTTPError, WalletError, ValueError) as exc:
        snap.error = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            client.close()
    return snap


def read_game_state(api: Any) -> dict[str, Any] | None:
    """Secondary reading: the in-game balance, when auth and geo allow it."""
    try:
        state = api.get_game_state()
    except Exception:
        return None
    return state if isinstance(state, dict) else None


@dataclass
class WalletWatcher:
    """Polls the chain on an interval and keeps the last good snapshot."""

    interval_s: float = 30.0
    address: str | None = None
    last: WalletSnapshot = field(default_factory=WalletSnapshot)
    _checked: float = 0.0

    def maybe_refresh(self, now: float, *, force: bool = False) -> WalletSnapshot | None:
        if not force and now - self._checked < self.interval_s:
            return None
        self._checked = now
        snap = read_wallet(self.address)
        # Keep the last good read rather than replacing it with an error, so a
        # transient RPC blip does not blank the operator's balance.
        if snap.ok or not self.last.ok:
            self.last = snap
        else:
            self.last.error = snap.error
            self.last.checked_at = snap.checked_at
        return self.last

    def to_dict(self) -> dict[str, Any]:
        return self.last.to_dict()
