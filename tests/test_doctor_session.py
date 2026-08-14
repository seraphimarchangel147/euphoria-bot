"""doctor.py reports session artefacts without inventing them. No network."""
from __future__ import annotations

from scripts import doctor as doctor_mod
from src.auth.session import save_session_artefacts


def test_doctor_session_warns_when_missing(capsys, monkeypatch):
    monkeypatch.setattr(doctor_mod.settings, "DRY_RUN", True)
    doctor_mod.check_session()
    out = capsys.readouterr().out
    assert "session botSignature" in out
    assert "not captured" in out
    assert "session deviceFingerprint" in out
    assert "session blob" in out
    assert "optional" in out
    # Must not fabricate values.
    assert "0x" not in out
    assert "[ ok ] session botSignature" not in out


def test_doctor_session_reports_present_from_file(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_mod.settings, "DRY_RUN", True)
    save_session_artefacts(
        {"botSignature": "0xcaptured-bot", "deviceFingerprint": "fp-captured"},
        store_path=tmp_path / "session.json",
    )
    doctor_mod.check_session()
    out = capsys.readouterr().out
    assert "[ ok ] session botSignature" in out
    assert "[ ok ] session deviceFingerprint" in out
    assert "from file" in out
    # Reports length, not the secret.
    assert "0xcaptured-bot" not in out
    assert "fp-captured" not in out


def test_doctor_session_fails_required_when_live(monkeypatch):
    monkeypatch.setattr(doctor_mod.settings, "DRY_RUN", False)
    doctor_mod._failed = False
    doctor_mod.check_session()
    assert doctor_mod._failed is True
