"""Wallet address resolution and on-chain balance reads. No network."""
import base64
import json

import httpx
import pytest

from config import settings
from src.monitor import wallet as w


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "header." + body + ".sig"


ADDR = "0x0e42Afc636d111017bedf662c03dc9175ba89b2E"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Never read the operator's real stores from a test."""
    monkeypatch.setattr(settings, "TOKEN_STORE", tmp_path / "tokens.json")
    monkeypatch.setattr(settings, "SESSION_STORE", tmp_path / "session.json")
    monkeypatch.delenv("EUPHORIA_WALLET_ADDRESS", raising=False)
    monkeypatch.setattr(settings, "PRIVATE_KEY", "")
    return tmp_path


# --- address resolution ---------------------------------------------------
def test_explicit_address_wins():
    addr, src = w.resolve_address(ADDR)
    assert addr == ADDR and src == "explicit"


def test_env_override_is_used(monkeypatch):
    monkeypatch.setenv("EUPHORIA_WALLET_ADDRESS", ADDR)
    assert w.resolve_address() == (ADDR, "env")


def test_address_is_read_from_a_privy_identity_token(_isolate):
    (_isolate / "tokens.json").write_text(json.dumps({
        "identity_token": _jwt({
            "sub": "did:privy:abc",
            "linked_accounts": [
                {"type": "google_oauth", "email": "someone@example.com"},
                {"type": "wallet", "address": ADDR, "chain_type": "ethereum"},
            ],
        })
    }))
    addr, src = w.resolve_address()
    assert addr == ADDR and src == "privy-token"


def test_extension_reported_address_beats_the_token(_isolate):
    (_isolate / "tokens.json").write_text(json.dumps({"identity_token": _jwt({"linked_accounts": [{"address": ADDR}]})}))
    other = "0x1111111111111111111111111111111111111111"
    (_isolate / "session.json").write_text(json.dumps({"wallet_address": other}))
    assert w.resolve_address() == (other, "extension")


def test_garbage_is_not_mistaken_for_an_address(_isolate):
    (_isolate / "session.json").write_text(json.dumps({"wallet_address": "0xnope"}))
    (_isolate / "tokens.json").write_text(json.dumps({"identity_token": "not-a-jwt"}))
    addr, src = w.resolve_address()
    assert addr == "" and src == "unknown"


def test_no_address_reports_a_usable_error():
    snap = w.read_wallet()
    assert not snap.ok
    assert "EUPHORIA_WALLET_ADDRESS" in snap.error
    assert snap.usdm is None


# --- chain reads ----------------------------------------------------------
def _rpc_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://rpc.test")


def test_balance_is_decoded_with_the_contract_decimals():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "eth_getBalance":
            return httpx.Response(200, json={"result": hex(2 * 10**18)})
        data = body["params"][0]["data"]
        if data.startswith(w.SEL_DECIMALS):
            return httpx.Response(200, json={"result": hex(6)})
        if data.startswith(w.SEL_BALANCE_OF):
            assert data.endswith(ADDR.lower().replace("0x", ""))
            return httpx.Response(200, json={"result": hex(1_234_500_000)})
        return httpx.Response(200, json={"result": "0x"})

    with _rpc_client(handler) as client:
        snap = w.read_wallet(ADDR, client=client, rpc_url="http://rpc.test")
    assert snap.ok
    assert snap.decimals == 6
    assert snap.usdm == pytest.approx(1234.5)
    assert snap.native == pytest.approx(2.0)


def test_absurd_decimals_fall_back_to_eighteen():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "eth_getBalance":
            return httpx.Response(200, json={"result": "0x0"})
        data = body["params"][0]["data"]
        if data.startswith(w.SEL_DECIMALS):
            return httpx.Response(200, json={"result": hex(99)})
        return httpx.Response(200, json={"result": hex(5 * 10**18)})

    with _rpc_client(handler) as client:
        snap = w.read_wallet(ADDR, client=client, rpc_url="http://rpc.test")
    assert snap.decimals == 18
    assert snap.usdm == pytest.approx(5.0)


def test_an_rpc_error_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": -32000, "message": "nope"}})

    with _rpc_client(handler) as client:
        snap = w.read_wallet(ADDR, client=client, rpc_url="http://rpc.test")
    assert not snap.ok
    assert "nope" in snap.error
    assert snap.address == ADDR


def test_an_empty_result_reads_as_zero_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "0x"})

    with _rpc_client(handler) as client:
        snap = w.read_wallet(ADDR, client=client, rpc_url="http://rpc.test")
    assert snap.ok
    assert snap.usdm == 0.0


# --- watcher --------------------------------------------------------------
def test_watcher_respects_its_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(w, "read_wallet", lambda addr: calls.append(addr) or w.WalletSnapshot(ok=True))
    watcher = w.WalletWatcher(interval_s=30.0)
    watcher.maybe_refresh(1000.0)
    watcher.maybe_refresh(1010.0)
    assert len(calls) == 1
    watcher.maybe_refresh(1040.0)
    assert len(calls) == 2


def test_a_transient_rpc_failure_does_not_blank_a_good_balance(monkeypatch):
    good = w.WalletSnapshot(address=ADDR, usdm=42.0, ok=True)
    bad = w.WalletSnapshot(address=ADDR, ok=False, error="boom")
    seq = [good, bad]
    monkeypatch.setattr(w, "read_wallet", lambda addr: seq.pop(0))
    watcher = w.WalletWatcher(interval_s=0.0)
    watcher.maybe_refresh(1000.0)
    watcher.maybe_refresh(1001.0)
    assert watcher.last.usdm == 42.0        # kept
    assert watcher.last.error == "boom"     # but the failure is surfaced
