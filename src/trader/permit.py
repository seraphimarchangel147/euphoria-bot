"""EIP-2612 USDM approvalPermit — signed in Python with the wallet key.

Verified on-chain (MegaETH 4326), not assumed
--------------------------------------------
The repo's USDM_ADDRESS (Euphoria Vault / EV) has no permit(). The token
the frontend permits is official MegaUSD:

    token:   0xFAfDdbb3FC7688494971a79cc65DCa3EF82079E7
    name:    "MegaUSD"
    version: "1"
    spender: 0x12759afcA690637b425ffbA3265F0Dc2F6242A8D  (exchange)
    value:   type(uint256).max
    types:   standard Permit(owner, spender, value, nonce, deadline)

A live exchange call (selector 0x6f7e758c) emitted Approval(owner, exchange,
max) on MegaUSD. Recovering that tx's v/r/s against the domain above matches
the owner if and only if spender is the exchange and value is max.

Nonce is MegaUSD.nonces(owner) via the MegaETH RPC. The RPC helper is
injectable so tests never hit the network.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address

from config import settings
from src.trader.eip712 import EIP712_DOMAIN_TYPE, _hex0x, address_for_key

PERMIT_TYPES: dict[str, list[dict[str, str]]] = {
    "Permit": [
        {"name": "owner", "type": "address"},
        {"name": "spender", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ]
}

PERMIT_DOMAIN: dict[str, Any] = {
    "name": "MegaUSD",
    "version": "1",
    "chainId": settings.CHAIN_ID,
    "verifyingContract": settings.USDM_PERMIT_TOKEN,
}

# Locked to the on-chain DOMAIN_SEPARATOR() of official MegaUSD.
ONCHAIN_DOMAIN_SEPARATOR = (
    "0x26e1aa8b35bf8653b60726926f670a6ec590674f00b149826914c480d0e798bd"
)

_NONCES_SELECTOR = "0x7ecebe00"
_UINT256_MAX = (1 << 256) - 1


class PermitError(ValueError):
    """Raised when a permit field is out of range or the nonce read fails."""


@dataclass(frozen=True)
class SignedPermit:
    message: dict[str, Any]
    signature: str
    owner: str


def permit_domain() -> dict[str, Any]:
    return dict(PERMIT_DOMAIN)


def typed_data(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": permit_domain(),
        "types": {**PERMIT_TYPES, "EIP712Domain": EIP712_DOMAIN_TYPE},
        "primaryType": "Permit",
        "message": message,
    }


def _as_address(value: str, name: str) -> str:
    text = (value or "").strip()
    if not text.startswith("0x") or len(text) != 42:
        raise PermitError(f"{name} must be a 20-byte 0x-address, got {value!r}")
    try:
        int(text, 16)
    except ValueError as exc:
        raise PermitError(f"{name} is not hex: {value!r}") from exc
    return to_checksum_address(text)


def _as_uint256(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PermitError(f"{name} must be an int, got {type(value).__name__}")
    if not 0 <= value <= _UINT256_MAX:
        raise PermitError(f"{name}={value} does not fit in uint256")
    return value


def validate_permit(message: dict[str, Any]) -> None:
    required = ("owner", "spender", "value", "nonce", "deadline")
    missing = [k for k in required if k not in message]
    if missing:
        raise PermitError(f"Permit missing fields: {missing}")
    extra = set(message) - set(required)
    if extra:
        raise PermitError(f"Permit has unexpected fields: {sorted(extra)}")
    _as_address(str(message["owner"]), "owner")
    _as_address(str(message["spender"]), "spender")
    _as_uint256(int(message["value"]), "value")
    _as_uint256(int(message["nonce"]), "nonce")
    deadline = _as_uint256(int(message["deadline"]), "deadline")
    if deadline == 0:
        raise PermitError("deadline is 0 -- refusing to sign an already-expired permit")


def build_permit(
    owner: str,
    *,
    spender: str | None = None,
    value: int | None = None,
    nonce: int,
    deadline: int | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    message = {
        "owner": _as_address(owner, "owner"),
        "spender": _as_address(spender or settings.PERMIT_SPENDER, "spender"),
        "value": _as_uint256(settings.PERMIT_VALUE if value is None else value, "value"),
        "nonce": _as_uint256(nonce, "nonce"),
        "deadline": _as_uint256(
            now + settings.PERMIT_DEADLINE_SECONDS if deadline is None else deadline,
            "deadline",
        ),
    }
    validate_permit(message)
    return message


def _eth_call(to: str, data: str, *, rpc_url: str | None = None) -> str:
    """JSON-RPC eth_call. Monkeypatch this in tests — never required on the network."""
    url = rpc_url or settings.RPC_URL
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": settings.USER_AGENT},
            timeout=15.0,
        )
        body = resp.json()
    except Exception as exc:
        raise PermitError(f"RPC eth_call failed: {exc}") from exc
    if resp.status_code >= 400:
        raise PermitError(f"RPC eth_call HTTP {resp.status_code}")
    if "error" in body:
        raise PermitError(f"RPC eth_call error: {body['error']}")
    result = body.get("result")
    if not result or result == "0x":
        raise PermitError("RPC eth_call returned empty result")
    return str(result)


def fetch_permit_nonce(
    owner: str,
    *,
    rpc_call: Callable[[str, str], str] | None = None,
) -> int:
    """Read MegaUSD.nonces(owner). `rpc_call(to, data)` is the test seam."""
    owner_addr = _as_address(owner, "owner")
    data = _NONCES_SELECTOR + owner_addr[2:].lower().zfill(64)
    fn = rpc_call or _eth_call
    raw = fn(settings.USDM_PERMIT_TOKEN, data)
    try:
        nonce = int(raw, 16)
    except ValueError as exc:
        raise PermitError(f"nonces() returned non-hex: {raw!r}") from exc
    return _as_uint256(nonce, "nonce")


def sign_usdm_permit(
    private_key: str,
    *,
    nonce: int | None = None,
    value: int | None = None,
    deadline: int | None = None,
    spender: str | None = None,
    rpc_call: Callable[[str, str], str] | None = None,
) -> SignedPermit:
    """Sign an EIP-2612 permit. Fetches the on-chain nonce unless one is passed."""
    owner = address_for_key(private_key)
    if nonce is None:
        nonce = fetch_permit_nonce(owner, rpc_call=rpc_call)
    message = build_permit(owner, spender=spender, value=value, nonce=nonce, deadline=deadline)
    signed = Account.sign_message(encode_typed_data(full_message=typed_data(message)), private_key)
    sig = _hex0x(signed.signature)
    if len(sig) != 132:
        raise PermitError(f"unexpected permit signature length {len(sig)}")
    return SignedPermit(message=message, signature=sig, owner=owner)


def recover_permit_signer(message: dict[str, Any], signature: str) -> str:
    validate_permit(message)
    return Account.recover_message(
        encode_typed_data(full_message=typed_data(message)), signature=signature
    )
