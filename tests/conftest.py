"""Test isolation.

A test must never read or write the operator's real state. Every on-disk store
lives under ``~/.euphoria`` by default, so a test that forgets to pass an
explicit path silently picks up the live calibration, the live equity curve, or
the live session cookies -- and writes test junk back over them.

This happened: a bankroll test wrote a fake 250.5 balance into the real ledger,
and the value then leaked into every later test in the file. Redirecting the
defaults for the whole suite is the fix that does not depend on remembering.
"""
from __future__ import annotations

import pytest

from config import settings

def _store_names() -> list[str]:
    """Every settings path that names an on-disk store.

    Discovered rather than listed. A hand-maintained list is only correct until
    the next store is added -- which is exactly what happened: TRAVERSAL_STORE
    and PNL_STORE were added after the list was written, the tests wrote a fake
    250.5 balance and a chain of test wallet values straight into the real
    ledger, and the operator's P&L then read -159.88 against a baseline that
    never existed.
    """
    return [n for n in dir(settings) if n.endswith("_STORE")]


@pytest.fixture(autouse=True)
def isolate_operator_state(tmp_path, monkeypatch):
    """Point every default store at this test's tmp dir.

    Autouse, so it applies even to tests that construct a ControlRoom without
    passing paths. Tests that pass explicit paths are unaffected.
    """
    store = tmp_path / "_state"
    store.mkdir(exist_ok=True)
    for name in _store_names():
        monkeypatch.setattr(settings, name, store / f"{name.lower()}.json")
    return store


def test_every_store_is_isolated():
    """Guard the guard: if a new store appears, it must be covered here."""
    names = set(_store_names())
    assert {"CALIBRATION_STORE", "BANKROLL_STORE", "TOKEN_STORE",
            "SESSION_STORE", "TRAVERSAL_STORE", "PNL_STORE"} <= names
