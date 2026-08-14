"""EIP-2612 MegaUSD permit — no network. Nonce/RPC is injected."""
from __future__ import annotations

import pytest
from eth_abi import encode
from eth_utils import keccak

from config import settings
from src.trader.permit import (
    ONCHAIN_DOMAIN_SEPARATOR,
    PERMIT_DOMAIN,
    PermitError,
    build_permit,
    fetch_permit_nonce,
    recover_permit_signer,
    sign_usdm_permit,
)

KEY = "0x" + "11" * 32


def _owner() -> str:
    from src.trader.eip712 import address_for_key
    return address_for_key(KEY)


def test_domain_matches_onchain_separator():
    """Lock the verified MegaUSD domain to the live DOMAIN_SEPARATOR()."""
    domain_typehash = keccak(
        text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    )
    digest = keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                domain_typehash,
                keccak(text="MegaUSD"),
                keccak(text="1"),
                4326,
                settings.USDM_PERMIT_TOKEN,
            ],
        )
    )
    assert "0x" + digest.hex() == ONCHAIN_DOMAIN_SEPARATOR
    assert PERMIT_DOMAIN["name"] == "MegaUSD"
    assert PERMIT_DOMAIN["version"] == "1"
    assert PERMIT_DOMAIN["chainId"] == 4326
    assert PERMIT_DOMAIN["verifyingContract"] == settings.USDM_PERMIT_TOKEN
    assert settings.PERMIT_SPENDER == settings.EXCHANGE_ADDRESS
    assert settings.PERMIT_VALUE == (1 << 256) - 1


def test_sign_and_recover_roundtrip():
    signed = sign_usdm_permit(KEY, nonce=0, deadline=2_000_000_000)
    assert signed.signature.startswith("0x") and len(signed.signature) == 132
    assert signed.owner.lower() == _owner().lower()
    assert signed.message["spender"].lower() == settings.EXCHANGE_ADDRESS.lower()
    assert signed.message["value"] == (1 << 256) - 1
    assert signed.message["nonce"] == 0
    rec = recover_permit_signer(signed.message, signed.signature)
    assert rec.lower() == signed.owner.lower()


def test_signature_is_deterministic_and_nonce_sensitive():
    a = sign_usdm_permit(KEY, nonce=0, deadline=2_000_000_000)
    b = sign_usdm_permit(KEY, nonce=0, deadline=2_000_000_000)
    c = sign_usdm_permit(KEY, nonce=1, deadline=2_000_000_000)
    assert a.signature == b.signature
    assert a.signature != c.signature


def test_wrong_spender_does_not_recover_owner():
    signed = sign_usdm_permit(KEY, nonce=0, deadline=2_000_000_000)
    tampered = dict(signed.message)
    tampered["spender"] = settings.USDM_ADDRESS
    rec = recover_permit_signer(tampered, signed.signature)
    assert rec.lower() != signed.owner.lower()


def test_uint256_overflow_rejected():
    with pytest.raises(PermitError, match="uint256"):
        build_permit(_owner(), nonce=0, value=1 << 256, deadline=1)
    with pytest.raises(PermitError, match="uint256"):
        build_permit(_owner(), nonce=-1, deadline=1)


def test_zero_deadline_rejected():
    with pytest.raises(PermitError, match="deadline"):
        build_permit(_owner(), nonce=0, deadline=0)


def test_bad_address_rejected():
    with pytest.raises(PermitError, match="owner"):
        build_permit("not-an-address", nonce=0, deadline=1)


def test_fetch_permit_nonce_uses_injected_rpc():
    calls: list[tuple[str, str]] = []

    def fake_rpc(to: str, data: str) -> str:
        calls.append((to, data))
        return "0x" + (7).to_bytes(32, "big").hex()

    nonce = fetch_permit_nonce(_owner(), rpc_call=fake_rpc)
    assert nonce == 7
    assert calls[0][0].lower() == settings.USDM_PERMIT_TOKEN.lower()
    assert calls[0][1].startswith("0x7ecebe00")


def test_sign_fetches_nonce_via_rpc_call_seam():
    signed = sign_usdm_permit(
        KEY,
        deadline=2_000_000_000,
        rpc_call=lambda to, data: "0x" + (3).to_bytes(32, "big").hex(),
    )
    assert signed.message["nonce"] == 3


def test_fetch_permit_nonce_rejects_empty_rpc():
    with pytest.raises(PermitError):
        fetch_permit_nonce(_owner(), rpc_call=lambda to, data: "0x")
