"""Cookie assembly for live API auth. No real tokens, no network."""
from __future__ import annotations

import json
import time

from src.auth.cookies import (
    DEFAULT_PRIVY_SESSION,
    assemble_cookies,
    env_cookie_fields,
    fields_from_mapping,
    format_cookie_header,
    load_cookie_fields,
    parse_cookie_header,
)
from src.auth.privy import PrivyAuth
from tests.test_auth import make_jwt


def test_parse_cookie_header_and_copy_as_curl_prefix():
    parsed = parse_cookie_header(
        "Cookie: privy-id-token=IDJWT; privy-token=ACCJWT; privy-session=privy.euphoria.finance; GCLB=abc"
    )
    assert parsed["privy-id-token"] == "IDJWT"
    assert parsed["privy-token"] == "ACCJWT"
    assert parsed["privy-session"] == "privy.euphoria.finance"
    assert parsed["GCLB"] == "abc"


def test_format_puts_privy_cookies_first():
    header = format_cookie_header({
        "GCLB": "abc",
        "privy-session": DEFAULT_PRIVY_SESSION,
        "privy-id-token": "ID",
        "privy-token": "ACC",
    })
    assert header.startswith("privy-token=ACC; privy-id-token=ID; privy-session=")
    assert "GCLB=abc" in header


def test_assemble_defaults_privy_session():
    cookies = assemble_cookies(identity_token="ID")
    assert cookies["privy-id-token"] == "ID"
    assert cookies["privy-session"] == DEFAULT_PRIVY_SESSION
    assert "privy-token" not in cookies


def test_env_individual_vars_win_over_cookie_header(monkeypatch):
    monkeypatch.setenv("EUPHORIA_COOKIE", "privy-id-token=FROMHEADER; privy-token=OLDACC")
    monkeypatch.setenv("EUPHORIA_PRIVY_ID_TOKEN", "FROMENV")
    fields = env_cookie_fields()
    assert fields["privy-id-token"] == "FROMENV"
    assert fields["privy-token"] == "OLDACC"


def test_load_cookie_fields_session_then_tokens(tmp_path):
    session = tmp_path / "session.json"
    tokens = tmp_path / "tokens.json"
    session.write_text(json.dumps({
        "privy-id-token": "from-session",
        "privy-token": "session-acc",
        "privyUserId": "did:privy:session",
        "botSignature": "0xnotacookie",
    }))
    tokens.write_text(json.dumps({
        "identity_token": "from-tokens",
        "access_token": "token-acc",
    }))
    fields = load_cookie_fields(session_path=session, token_path=tokens)
    assert fields["privy-id-token"] == "from-tokens"  # tokens.json overlays
    assert fields["privy-token"] == "token-acc"
    assert fields["privyUserId"] == "did:privy:session"
    assert "botSignature" not in fields


def test_privy_auth_from_euphoria_cookie_env(tmp_path, monkeypatch):
    tok = make_jwt(time.time() + 3600, sub="did:privy:abc123")
    monkeypatch.delenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", raising=False)
    monkeypatch.setenv(
        "EUPHORIA_COOKIE",
        f"privy-id-token={tok}; privy-token=acc; privy-session=privy.euphoria.finance",
    )
    auth = PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")
    headers = auth.auth_headers()
    assert "Authorization" not in headers
    assert f"privy-id-token={tok}" in headers["Cookie"]
    assert "privy-token=acc" in headers["Cookie"]
    assert auth.privy_user_id() == "did:privy:abc123"


def test_privy_auth_from_session_file(tmp_path, monkeypatch):
    monkeypatch.delenv("EUPHORIA_PRIVY_IDENTITY_TOKEN", raising=False)
    monkeypatch.delenv("EUPHORIA_PRIVY_REFRESH_TOKEN", raising=False)
    tok = make_jwt(time.time() + 3600, sub="did:privy:fileuser")
    (tmp_path / "s.json").write_text(json.dumps({
        "privy-id-token": tok,
        "privy-token": "file-acc",
        "privyUserId": "did:privy:explicit",
    }))
    auth = PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")
    assert auth.privy_user_id() == "did:privy:explicit"
    assert "privy-token=file-acc" in auth.cookie_header()


def test_env_user_id_wins_over_jwt_sub(tmp_path, monkeypatch):
    tok = make_jwt(time.time() + 3600, sub="did:privy:fromjwt")
    monkeypatch.setenv("EUPHORIA_PRIVY_ID_TOKEN", tok)
    monkeypatch.setenv("EUPHORIA_PRIVY_USER_ID", "did:privy:fromenv")
    auth = PrivyAuth(store_path=tmp_path / "t.json", session_path=tmp_path / "s.json")
    assert auth.privy_user_id() == "did:privy:fromenv"


def test_fields_from_mapping_ignores_empty():
    assert fields_from_mapping({"privy-id-token": "  ", "privy-token": None}) == {}
