"""Session-artefact store: pass-through only, no inventing, no private keys."""
from __future__ import annotations

import json
import stat

from config import settings
from src.auth.session import (
    load_session_artefacts,
    save_session_artefacts,
)


def test_load_empty_when_nothing_configured(tmp_path):
    arts = load_session_artefacts(store_path=tmp_path / "missing.json")
    assert arts.as_payload() == {}
    assert arts.botSignature == ""
    assert arts.deviceFingerprint == ""
    assert arts.blob == ""


def test_load_from_session_file(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "botSignature": "0xfile-bot",
        "deviceFingerprint": "fp-file",
        "blob": "blob-file",
    }))
    arts = load_session_artefacts(store_path=path)
    assert arts.as_payload() == {
        "botSignature": "0xfile-bot",
        "deviceFingerprint": "fp-file",
        "blob": "blob-file",
    }
    assert arts.sources["botSignature"] == "file"


def test_load_accepts_snake_case_aliases(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "bot_signature": "0xsnake",
        "device_fingerprint": "fp-snake",
    }))
    arts = load_session_artefacts(store_path=path)
    assert arts.botSignature == "0xsnake"
    assert arts.deviceFingerprint == "fp-snake"


def test_env_wins_over_session_file(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "botSignature": "0xfile-bot",
        "deviceFingerprint": "fp-file",
        "blob": "blob-file",
    }))
    monkeypatch.setenv("EUPHORIA_BOT_SIGNATURE", "0xenv-bot")
    monkeypatch.setenv("EUPHORIA_DEVICE_FINGERPRINT", "fp-env")
    arts = load_session_artefacts(store_path=path)
    assert arts.botSignature == "0xenv-bot"
    assert arts.deviceFingerprint == "fp-env"
    assert arts.blob == "blob-file"  # env not set → file
    assert arts.sources["botSignature"] == "env"
    assert arts.sources["blob"] == "file"


def test_settings_used_when_env_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BOT_SIGNATURE", "0xsettings-bot")
    monkeypatch.setattr(settings, "DEVICE_FINGERPRINT", "fp-settings")
    monkeypatch.setattr(settings, "BLOB", "blob-settings")
    arts = load_session_artefacts(store_path=tmp_path / "missing.json")
    assert arts.as_payload() == {
        "botSignature": "0xsettings-bot",
        "deviceFingerprint": "fp-settings",
        "blob": "blob-settings",
    }
    assert arts.sources["botSignature"] == "settings"


def test_empty_strings_stay_missing(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "botSignature": "   ",
        "deviceFingerprint": None,
        "blob": "",
    }))
    monkeypatch.setenv("EUPHORIA_BOT_SIGNATURE", "")
    arts = load_session_artefacts(store_path=path)
    assert arts.as_payload() == {}


def test_malformed_file_is_ignored(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("not-json{")
    assert load_session_artefacts(store_path=path).as_payload() == {}


def test_load_ignores_private_keys(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "botSignature": "0xbot",
        "private_key": "0xsecret",
        "EUPHORIA_PRIVATE_KEY": "0xsecret",
        "refresh_token": "steal-me",
    }))
    payload = load_session_artefacts(store_path=path).as_payload()
    assert payload == {"botSignature": "0xbot"}
    assert "private_key" not in payload
    assert "refresh_token" not in payload


def test_save_strips_private_keys_and_sets_0600(tmp_path):
    path = tmp_path / "nested" / "session.json"
    save_session_artefacts(
        {
            "botSignature": "0xbot",
            "deviceFingerprint": "fp",
            "blob": "blob",
            "private_key": "0xsecret",
            "EUPHORIA_PRIVATE_KEY": "0xsecret",
        },
        store_path=path,
    )
    data = json.loads(path.read_text())
    assert data == {
        "botSignature": "0xbot",
        "deviceFingerprint": "fp",
        "blob": "blob",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_merges_and_does_not_write_empty_keys(tmp_path):
    path = tmp_path / "session.json"
    save_session_artefacts({"botSignature": "0xold", "blob": "keep"}, store_path=path)
    save_session_artefacts({"botSignature": "0xnew"}, store_path=path)
    data = json.loads(path.read_text())
    assert data["botSignature"] == "0xnew"
    assert data["blob"] == "keep"
    assert "deviceFingerprint" not in data
