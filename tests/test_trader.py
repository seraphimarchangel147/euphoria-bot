"""Regression tests for bugs found during end-to-end verification."""
import base64
import json
import time

import pytest

from src.trader import eip712
from src.trader.api import EuphoriaAPIError, TradeRequest
from src.trader.auto_trader import EuphoriaTrader
from src.trader.risk import RiskEngine, RiskRejection

KEY = "0x" + "11" * 32


@pytest.fixture(autouse=True)
def fake_identity(monkeypatch):
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": time.time() + 3600}).encode()
    ).decode().rstrip("=")
    monkeypatch.setenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", f"h.{payload}.s")
    monkeypatch.setenv("EUPHORIA_PRIVATE_KEY", KEY)


def make_trader(**kw):
    # proxy="" means "no proxy, and do NOT run network discovery"
    kw.setdefault("proxy", "")
    kw.setdefault("dry_run", True)
    kw.setdefault("risk", RiskEngine(max_trade=10, max_daily_loss=50, max_open=3))
    return EuphoriaTrader(private_key=KEY, **kw)


def test_dry_run_never_scans_for_a_proxy(monkeypatch):
    """REGRESSION: constructor used to hang for minutes on proxy discovery."""
    import src.trader.auto_trader as at

    def boom():
        raise AssertionError("proxy discovery must not run implicitly")

    monkeypatch.setattr(at, "find_working_proxy", boom)
    trader = EuphoriaTrader(private_key=KEY, dry_run=True)
    assert trader.proxy is None
    trader.close()


def test_explicit_empty_proxy_disables_discovery(monkeypatch):
    import src.trader.auto_trader as at
    monkeypatch.setattr(at, "find_working_proxy", lambda: (_ for _ in ()).throw(AssertionError()))
    t = make_trader(dry_run=False)
    assert t.proxy is None
    t.close()


def test_prepare_signs_and_self_verifies():
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=2.5, start_price=1884.17,
                       price_interval=0.94, time_interval_seconds=5)
    signed = t.prepare(req)
    assert signed.signature.startswith("0x") and len(signed.signature) == 132
    assert signed.signer.lower() == eip712.address_for_key(KEY).lower()
    assert signed.payload["timeInterval"] == 5000        # seconds -> ms
    assert signed.payload["nonce"] == signed.message["nonce"]
    t.close()


def test_prepare_does_not_invent_missing_artefacts():
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req)
    assert "botSignature" not in signed.payload
    assert "deviceFingerprint" not in signed.payload
    assert "blob" not in signed.payload
    # approvalPermit is signed locally — that is not inventing a browser artefact.
    assert signed.payload["approvalPermit"].startswith("0x")
    assert len(signed.payload["approvalPermit"]) == 132
    t.close()


def test_prepare_attaches_approval_permit_from_key():
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req)
    from src.trader.permit import recover_permit_signer, sign_usdm_permit
    expected = sign_usdm_permit(KEY, nonce=0)
    # deadline is time-based; recover the attached sig against a freshly built message
    rec = recover_permit_signer(expected.message, expected.signature)
    assert rec.lower() == signed.signer.lower()
    assert signed.payload["approvalPermit"].startswith("0x")
    t.close()


def test_prepare_caller_approval_permit_wins():
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req, approvalPermit="0xcaller-permit")
    assert signed.payload["approvalPermit"] == "0xcaller-permit"
    t.close()


def test_prepare_leaves_permit_missing_when_nonce_read_fails(monkeypatch):
    from src.trader.permit import PermitError
    monkeypatch.setattr(
        "src.trader.auto_trader.sign_usdm_permit",
        lambda *a, **k: (_ for _ in ()).throw(PermitError("RPC down")),
    )
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req)
    assert "approvalPermit" not in signed.payload
    t.close()


def test_prepare_attaches_artefacts_from_env(monkeypatch):
    monkeypatch.setenv("EUPHORIA_BOT_SIGNATURE", "0xenv-bot")
    monkeypatch.setenv("EUPHORIA_DEVICE_FINGERPRINT", "fp-env")
    monkeypatch.setenv("EUPHORIA_BLOB", "blob-env")
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req)
    assert signed.payload["botSignature"] == "0xenv-bot"
    assert signed.payload["deviceFingerprint"] == "fp-env"
    assert signed.payload["blob"] == "blob-env"
    t.close()


def test_prepare_attaches_artefacts_from_settings(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "BOT_SIGNATURE", "0xsettings-bot")
    monkeypatch.setattr(settings, "DEVICE_FINGERPRINT", "fp-settings")
    monkeypatch.setattr(settings, "BLOB", "blob-settings")
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req)
    assert signed.payload["botSignature"] == "0xsettings-bot"
    assert signed.payload["deviceFingerprint"] == "fp-settings"
    assert signed.payload["blob"] == "blob-settings"
    t.close()


def test_prepare_attaches_artefacts_from_session_file(tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({
        "botSignature": "0xfile-bot",
        "deviceFingerprint": "fp-file",
        "blob": "blob-file",
    }))
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req)
    assert signed.payload["botSignature"] == "0xfile-bot"
    assert signed.payload["deviceFingerprint"] == "fp-file"
    assert signed.payload["blob"] == "blob-file"
    t.close()


def test_prepare_caller_extra_wins_over_session(monkeypatch):
    monkeypatch.setenv("EUPHORIA_BOT_SIGNATURE", "0xenv-bot")
    monkeypatch.setenv("EUPHORIA_DEVICE_FINGERPRINT", "fp-env")
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    signed = t.prepare(req, botSignature="0xcaller-bot")
    assert signed.payload["botSignature"] == "0xcaller-bot"
    assert signed.payload["deviceFingerprint"] == "fp-env"
    t.close()


def test_dry_run_is_default_even_with_session_artefacts(monkeypatch):
    monkeypatch.setenv("EUPHORIA_BOT_SIGNATURE", "0xenv-bot")
    monkeypatch.setenv("EUPHORIA_DEVICE_FINGERPRINT", "fp-env")
    t = EuphoriaTrader(private_key=KEY, proxy="", risk=RiskEngine(max_trade=10, max_daily_loss=50, max_open=3))
    assert t.dry_run is True
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    result = t.submit(t.prepare(req))
    assert result["dryRun"] is True
    assert result["payload"]["botSignature"] == "0xenv-bot"
    t.close()


def test_live_submit_with_key_only_names_browser_artefacts():
    """Permit is attached; execute_trade should only name Turnstile / fingerprint."""
    t = make_trader(dry_run=False)
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    with pytest.raises(EuphoriaAPIError) as exc:
        t.submit(t.prepare(req))
    msg = str(exc.value)
    assert msg == (
        "executeTrade payload is incomplete, missing: botSignature, deviceFingerprint"
    )
    assert "approvalPermit" not in msg
    t.close()


def test_live_submit_with_session_and_permit_clears_the_guard(monkeypatch):
    monkeypatch.setenv("EUPHORIA_BOT_SIGNATURE", "0xenv-bot")
    monkeypatch.setenv("EUPHORIA_DEVICE_FINGERPRINT", "fp-env")
    t = make_trader(dry_run=False)
    t.api.mutate = lambda proc, payload: {"ok": True, "procedure": proc}
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    result = t.submit(t.prepare(req))
    assert result["ok"] is True
    t.close()


def test_risk_blocks_before_signing():
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=999999, start_price=1884.0, price_interval=1.0)
    with pytest.raises(RiskRejection):
        t.prepare(req)
    t.close()


def test_dry_run_submit_does_not_hit_network():
    t = make_trader()
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    result = t.submit(t.prepare(req))
    assert result["dryRun"] is True
    assert result["payload"]["asset"] == "ETH"
    t.close()


def test_live_submit_names_every_missing_browser_artefact():
    t = make_trader(dry_run=False)
    req = TradeRequest(asset="ETH", amount=1.0, start_price=1884.0, price_interval=1.0)
    with pytest.raises(EuphoriaAPIError) as exc:
        t.submit(t.prepare(req))
    for field in ("botSignature", "deviceFingerprint"):
        assert field in str(exc.value)
    assert "approvalPermit" not in str(exc.value)
    t.close()


def test_huge_amount_raises_validation_not_decimal_error():
    """REGRESSION: 2**128 used to raise decimal.InvalidOperation."""
    with pytest.raises(eip712.OrderValidationError):
        eip712.build_taker_order("ETH", 2 ** 128, int(time.time()), 5, 3000.0, 0.5)


def test_nonsense_amount_string_raises_validation_error():
    with pytest.raises(eip712.OrderValidationError):
        eip712.build_taker_order("ETH", "not-a-number", int(time.time()), 5, 3000.0, 0.5)
