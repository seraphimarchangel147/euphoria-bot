"""doctor.py reports cookie presence without printing secrets. No live API."""
from __future__ import annotations

import time

from scripts import doctor as doctor_mod
from tests.test_auth import make_jwt


def test_doctor_auth_reports_cookie_lengths_not_values(capsys, tmp_path, monkeypatch):
    tok = make_jwt(time.time() + 3600, sub="did:privy:doctor")
    monkeypatch.setenv("EUPHORIA_PRIVY_ID_TOKEN", tok)
    monkeypatch.setenv("EUPHORIA_PRIVY_TOKEN", "access-secret-value")
    monkeypatch.delenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.privy.settings.TOKEN_STORE", tmp_path / "t.json")
    monkeypatch.setattr("src.auth.privy.settings.SESSION_STORE", tmp_path / "s.json")

    class FakeAPI:
        def __init__(self, auth, proxy=None):
            self.auth = auth
        def whoami(self):
            return {"id": "ok"}
        def close(self):
            pass

    monkeypatch.setattr("src.trader.api.EuphoriaAPI", FakeAPI)
    doctor_mod._failed = False
    doctor_mod.check_auth()
    out = capsys.readouterr().out
    assert "[ ok ] API cookies" in out
    assert "privy-id-token" in out
    assert "chars" in out
    assert "[ ok ] users.getProfile" in out
    assert tok not in out
    assert "access-secret-value" not in out
