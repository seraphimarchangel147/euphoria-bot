"""Isolate every test from a real ~/.euphoria/session.json or artefact env."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_session_store(tmp_path, monkeypatch):
    monkeypatch.delenv("EUPHORIA_BOT_SIGNATURE", raising=False)
    monkeypatch.delenv("EUPHORIA_DEVICE_FINGERPRINT", raising=False)
    monkeypatch.delenv("EUPHORIA_BLOB", raising=False)
    store = tmp_path / "session.json"
    monkeypatch.setenv("EUPHORIA_SESSION_STORE", str(store))
    monkeypatch.setattr("config.settings.BOT_SIGNATURE", "")
    monkeypatch.setattr("config.settings.DEVICE_FINGERPRINT", "")
    monkeypatch.setattr("config.settings.BLOB", "")
    monkeypatch.setattr("config.settings.SESSION_STORE", store)
    return store


@pytest.fixture(autouse=True)
def mock_permit_rpc(monkeypatch):
    """Permit nonce reads must never hit MegaETH RPC in tests."""
    monkeypatch.setattr(
        "src.trader.permit._eth_call",
        lambda to, data, **k: "0x" + (0).to_bytes(32, "big").hex(),
    )
