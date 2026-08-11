"""EIP-712 TakerOrder construction and signing for Euphoria Finance.

Fixes over the first draft
--------------------------
* signature hex is normalised to a 0x-prefixed string (eth_account >= 0.13
  returns HexBytes whose .hex() has NO 0x prefix -- the old code emitted
  "0x" + already-unprefixed hex only by accident and broke on older versions).
* amount/price encoding uses Decimal so 0.1-style floats do not truncate a wei.
* every field is range-checked against its solidity type before signing; an
  out-of-range uint silently wrapping is the classic way to sign an order that
  means something entirely different from what you intended.
* timeInterval is documented and validated in SECONDS (uint32); the API payload
  separately carries milliseconds.
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from config import settings

EIP712_DOMAIN: dict[str, Any] = dict(settings.EIP712_DOMAIN)

TAKER_ORDER_TYPES: dict[str, list[dict[str, str]]] = {
    "TakerOrder": [
        {"name": "takerAmount", "type": "uint128"},
        {"name": "underlying", "type": "uint8"},
        {"name": "nonce", "type": "uint8"},
        {"name": "startTime", "type": "uint64"},
        {"name": "timeInterval", "type": "uint32"},
        {"name": "startPrice", "type": "uint64"},
        {"name": "priceInterval", "type": "uint64"},
    ]
}

EIP712_DOMAIN_TYPE = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

ASSETS: dict[str, int] = dict(settings.ASSETS)

_UINT_BOUNDS = {"uint8": 8, "uint32": 32, "uint64": 64, "uint128": 128}


class OrderValidationError(ValueError):
    """Raised when a TakerOrder field would overflow or is nonsensical."""


def get_nonce(now_ms: int | None = None) -> int:
    """Frontend nonce: floor((Date.now() % 5120) / 20) -> 0..255 (fits uint8)."""
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return (ms % 5120) // 20


def _scale(value: float | int | str | Decimal, decimals: int) -> int:
    """Exact decimal scaling; truncates sub-unit dust rather than rounding up.

    Uses a wide local context so very large inputs raise a clean
    OrderValidationError from validate_order rather than decimal.InvalidOperation.
    """
    try:
        with localcontext() as ctx:
            ctx.prec = 80
            dec = Decimal(str(value))
            quantum = Decimal(1).scaleb(-decimals)
            scaled = dec.quantize(quantum, rounding=ROUND_DOWN) * Decimal(10) ** decimals
            return int(scaled)
    except (InvalidOperation, ArithmeticError, ValueError) as exc:
        raise OrderValidationError(f"cannot encode value {value!r}: {exc}") from exc


def encode_amount(amount: float | int | str | Decimal) -> int:
    """USDM amount -> uint128 (18 decimals)."""
    return _scale(amount, settings.AMOUNT_DECIMALS)


def encode_price(price: float | int | str | Decimal) -> int:
    """Price -> uint64 (8 decimals)."""
    return _scale(price, settings.PRICE_DECIMALS)


def validate_order(message: dict[str, int]) -> None:
    """Range-check every field against its declared solidity type."""
    fields = {f["name"]: f["type"] for f in TAKER_ORDER_TYPES["TakerOrder"]}
    missing = set(fields) - set(message)
    if missing:
        raise OrderValidationError(f"TakerOrder missing fields: {sorted(missing)}")
    extra = set(message) - set(fields)
    if extra:
        raise OrderValidationError(f"TakerOrder has unexpected fields: {sorted(extra)}")

    for name, sol_type in fields.items():
        value = message[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise OrderValidationError(f"{name} must be an int, got {type(value).__name__}")
        bits = _UINT_BOUNDS[sol_type]
        if not 0 <= value < (1 << bits):
            raise OrderValidationError(
                f"{name}={value} does not fit in {sol_type} (max {(1 << bits) - 1})"
            )

    if message["takerAmount"] == 0:
        raise OrderValidationError("takerAmount is 0 -- refusing to sign an empty order")
    if message["timeInterval"] == 0:
        raise OrderValidationError("timeInterval is 0 seconds")
    if message["startPrice"] == 0:
        raise OrderValidationError("startPrice is 0 -- oracle read probably failed")
    if message["underlying"] not in ASSETS.values():
        raise OrderValidationError(
            f"underlying={message['underlying']} is not a known asset id {sorted(ASSETS.values())}"
        )


def build_taker_order(
    asset: str,
    amount: float | str | Decimal,
    start_time: int,
    time_interval_seconds: int,
    start_price: float | str | Decimal,
    price_interval: float | str | Decimal,
    nonce: int | None = None,
) -> dict[str, int]:
    """Build a validated EIP-712 TakerOrder message.

    `time_interval_seconds` is SECONDS (the on-chain uint32). The tRPC payload
    uses milliseconds -- see trader.api.to_payload.
    """
    underlying = ASSETS.get(asset.upper())
    if underlying is None:
        raise OrderValidationError(f"Unknown asset {asset!r}; supported: {sorted(ASSETS)}")

    message = {
        "takerAmount": encode_amount(amount),
        "underlying": underlying,
        "nonce": get_nonce() if nonce is None else int(nonce),
        "startTime": int(start_time),
        "timeInterval": int(time_interval_seconds),
        "startPrice": encode_price(start_price),
        "priceInterval": encode_price(price_interval),
    }
    validate_order(message)
    return message


def typed_data(message: dict[str, int]) -> dict[str, Any]:
    return {
        "domain": EIP712_DOMAIN,
        "types": {**TAKER_ORDER_TYPES, "EIP712Domain": EIP712_DOMAIN_TYPE},
        "primaryType": "TakerOrder",
        "message": message,
    }


def _hex0x(value: Any) -> str:
    """Normalise HexBytes/bytes/str to a 0x-prefixed lowercase hex string."""
    if isinstance(value, (bytes, bytearray)):
        h = bytes(value).hex()
    else:
        h = str(value)
    h = h[2:] if h.startswith("0x") else h
    return "0x" + h.lower()


def sign_order(private_key: str, message: dict[str, int]) -> str:
    """Sign a TakerOrder. Returns a 0x-prefixed 65-byte signature (132 chars)."""
    validate_order(message)
    signed = Account.sign_message(encode_typed_data(full_message=typed_data(message)), private_key)
    sig = _hex0x(signed.signature)
    if len(sig) != 132:
        raise OrderValidationError(f"unexpected signature length {len(sig)}: {sig[:20]}...")
    return sig


def recover_signer(message: dict[str, int], signature: str) -> str:
    """Recover the signing address -- use this to self-verify before submitting."""
    return Account.recover_message(
        encode_typed_data(full_message=typed_data(message)), signature=signature
    )


def address_for_key(private_key: str) -> str:
    return Account.from_key(private_key).address
