"""doctor.py reports permit readiness without printing secrets. No network."""
from __future__ import annotations

from scripts import doctor as doctor_mod

KEY = "0x" + "11" * 32


def test_doctor_permit_ok_without_printing_full_sig(capsys, monkeypatch):
    monkeypatch.setattr(doctor_mod.settings, "PRIVATE_KEY", KEY)
    doctor_mod._failed = False
    doctor_mod.check_permit()
    out = capsys.readouterr().out
    assert "[ ok ] USDM EIP-2612 permit" in out
    assert "spender exchange" in out
    # Prefix only — never the full 65-byte signature.
    assert "sig 0x" in out
    assert "..." in out
    full_sig_chars = 132
    assert not any(len(part) == full_sig_chars and part.startswith("0x") for part in out.split())


def test_doctor_permit_warns_without_key(capsys, monkeypatch):
    monkeypatch.setattr(doctor_mod.settings, "PRIVATE_KEY", "")
    doctor_mod.check_permit()
    out = capsys.readouterr().out
    assert "[warn] USDM permit" in out
    assert "no wallet key" in out
