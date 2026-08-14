import json
import time

import pytest

from src.auth import privy
from src.auth.privy import PrivyAuth, PrivyAuthError, TokenBundle, _jwt_exp


def make_jwt(exp: float, **claims) -> str:
    import base64
    payload = {"exp": exp, **claims}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.sig"


def test_jwt_exp_parsed():
    assert _jwt_exp(make_jwt(1700000000)) == 1700000000


def test_jwt_exp_is_zero_on_garbage():
    assert _jwt_exp("not-a-jwt") == 0.0


def test_bundle_freshness_respects_margin():
    assert TokenBundle("tok", expires_at=time.time() + 3600).is_fresh
    assert not TokenBundle("tok", expires_at=time.time() + 60).is_fresh   # inside margin
    assert not TokenBundle("", expires_at=time.time() + 3600).is_fresh


def test_requires_some_credential(tmp_path, monkeypatch):
    monkeypatch.delenv("EUPHORIA_PRIVY_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", raising=False)
    monkeypatch.delenv("EUPHORIA_PRIVY_ID_TOKEN", raising=False)
    monkeypatch.delenv("EUPHORIA_PRIVY_TOKEN", raising=False)
    monkeypatch.delenv("EUPHORIA_COOKIE", raising=False)
    with pytest.raises(PrivyAuthError, match="No Privy credentials"):
        PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")


def test_direct_identity_token_sent_as_cookie_not_bearer(tmp_path, monkeypatch):
    tok = make_jwt(time.time() + 3600)
    monkeypatch.setenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", tok)
    auth = PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")
    assert auth.identity_token() == tok
    headers = auth.auth_headers()
    assert "Authorization" not in headers
    assert headers["Cookie"].startswith("privy-id-token=" + tok)
    assert "privy-session=privy.euphoria.finance" in headers["Cookie"]


def test_expired_identity_without_refresh_is_still_sent_as_cookie(tmp_path, monkeypatch):
    """Cookie-only sessions are pass-through; the API decides if they are stale."""
    monkeypatch.delenv("EUPHORIA_PRIVY_REFRESH_TOKEN", raising=False)
    tok = make_jwt(time.time() - 10)
    monkeypatch.setenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", tok)
    auth = PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")
    assert auth.identity_token() == tok
    assert tok in auth.auth_headers()["Cookie"]


def test_refresh_persists_rotated_token(tmp_path, monkeypatch):
    monkeypatch.setenv("EUPHORIA_PRIVY_REFRESH_TOKEN", "refresh-1")
    monkeypatch.delenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", raising=False)
    store = tmp_path / "t.json"
    auth = PrivyAuth(store_path=store, session_path=tmp_path / "s.json")

    new_identity = make_jwt(time.time() + 3600)

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"identity_token": new_identity, "token": "access-1", "refresh_token": "refresh-2"}

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(privy.http_util, "build_client", lambda **kw: FakeClient())
    monkeypatch.setattr(privy.http_util, "request", lambda *a, **k: FakeResp())

    assert auth.identity_token() == new_identity
    saved = json.loads(store.read_text())
    assert saved["refresh_token"] == "refresh-2"     # rotation persisted
    assert saved["identity_token"] == new_identity
    headers = auth.auth_headers()
    assert "Authorization" not in headers
    assert f"privy-id-token={new_identity}" in headers["Cookie"]
    assert "privy-token=access-1" in headers["Cookie"]
    assert "privy-session=privy.euphoria.finance" in headers["Cookie"]


def test_refresh_without_identity_token_in_response_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EUPHORIA_PRIVY_REFRESH_TOKEN", "refresh-1")
    monkeypatch.delenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", raising=False)
    auth = PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")

    class FakeResp:
        status_code = 200
        text = ""
        def json(self): return {"token": "access-only"}

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(privy.http_util, "build_client", lambda **kw: FakeClient())
    monkeypatch.setattr(privy.http_util, "request", lambda *a, **k: FakeResp())
    with pytest.raises(PrivyAuthError, match="no identity_token"):
        auth.identity_token()
