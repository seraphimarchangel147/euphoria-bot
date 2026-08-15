"""Cookie / artefact session store for the operator control room.

Accepts Privy cookies from env, ~/.euphoria/tokens.json, or a localhost POST
from the Chrome extension. Does not scrape the browser profile, talk CDP, or
replace the existing refresh-token path in src.auth.privy.

Writes are mode 0600. Callers must never log cookie or artefact values.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from config import settings

log = logging.getLogger("euphoria.session")

COOKIE_NAMES = ("privy-token", "privy-id-token", "privy-session")
ARTEFACT_KEYS = ("botSignature", "deviceFingerprint", "approvalPermit", "blob")
_ENV_COOKIE = {
    "privy-token": "EUPHORIA_PRIVY_TOKEN",
    "privy-id-token": "EUPHORIA_PRIVY_ID_TOKEN",
    "privy-session": "EUPHORIA_PRIVY_SESSION",
}


def session_store_path() -> Path:
    return Path(getattr(settings, "SESSION_STORE", Path.home() / ".euphoria" / "session.json"))


def token_store_path() -> Path:
    return Path(settings.TOKEN_STORE)


@dataclass
class SessionState:
    cookies: dict[str, str] = field(default_factory=dict)
    privy_user_id: str = ""
    artefacts: dict[str, str] = field(default_factory=dict)
    updated_at: float = 0.0

    def cookie_names(self) -> list[str]:
        return sorted(k for k, v in self.cookies.items() if v and k in COOKIE_NAMES)

    def artefact_names(self) -> list[str]:
        return sorted(k for k, v in self.artefacts.items() if v and k in ARTEFACT_KEYS)

    def has_live_artefacts(self) -> bool:
        return all(self.artefacts.get(k) for k in ("botSignature", "deviceFingerprint", "approvalPermit"))

    def public_view(self) -> dict[str, Any]:
        """Status fields only — no secret values."""
        return {
            "has_cookies": bool(self.cookie_names()),
            "cookie_names": self.cookie_names(),
            "privy_user_id_set": bool(self.privy_user_id),
            "has_artefacts": self.has_live_artefacts(),
            "artefact_names": self.artefact_names(),
            "updated_at": self.updated_at,
        }


def cookies_from_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, env_key in _ENV_COOKIE.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            out[name] = val
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _pick_cookies(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for name in COOKIE_NAMES:
        val = raw.get(name)
        if isinstance(val, str) and val.strip():
            out[name] = val.strip()
    return out


def _pick_artefacts(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ARTEFACT_KEYS:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def load_session(
    *,
    session_path: Path | None = None,
    token_path: Path | None = None,
) -> SessionState:
    session_path = session_path or session_store_path()
    token_path = token_path or token_store_path()
    token_data = _read_json(token_path)
    session_data = _read_json(session_path)

    cookies = cookies_from_env()
    cookies.update(_pick_cookies(token_data.get("cookies")))
    cookies.update(_pick_cookies(session_data.get("cookies")))

    uid = (
        os.environ.get("EUPHORIA_PRIVY_USER_ID", "").strip()
        or str(session_data.get("privyUserId") or token_data.get("privyUserId") or "")
    )
    artefacts = _pick_artefacts(token_data)
    artefacts.update(_pick_artefacts(session_data.get("artefacts") or session_data))

    updated = float(session_data.get("updated_at") or token_data.get("updated_at") or 0.0)
    return SessionState(cookies=cookies, privy_user_id=uid, artefacts=artefacts, updated_at=updated)


def persist_session(
    state: SessionState,
    *,
    session_path: Path | None = None,
    token_path: Path | None = None,
) -> None:
    """Write cookies to tokens.json (merged) and session.json. Mode 0600."""
    session_path = session_path or session_store_path()
    token_path = token_path or token_store_path()

    token_data = _read_json(token_path)
    existing = token_data.get("cookies") if isinstance(token_data.get("cookies"), dict) else {}
    merged_cookies = {k: v for k, v in existing.items() if isinstance(v, str) and v}
    merged_cookies.update(state.cookies)
    token_data["cookies"] = {k: merged_cookies[k] for k in COOKIE_NAMES if merged_cookies.get(k)}
    if state.privy_user_id:
        token_data["privyUserId"] = state.privy_user_id
    if state.updated_at:
        token_data["session_updated_at"] = state.updated_at
    _atomic_write(token_path, token_data)

    session_data = {
        "cookies": {k: state.cookies[k] for k in COOKIE_NAMES if state.cookies.get(k)},
        "privyUserId": state.privy_user_id,
        "artefacts": {k: state.artefacts[k] for k in ARTEFACT_KEYS if state.artefacts.get(k)},
        "updated_at": state.updated_at,
    }
    _atomic_write(session_path, session_data)
    log.info(
        "session saved: cookies=%s privyUserId=%s artefacts=%s",
        state.cookie_names(),
        "set" if state.privy_user_id else "absent",
        state.artefact_names(),
    )


def apply_payload(state: SessionState, payload: dict[str, Any], *, now: float) -> bool:
    """Merge a /session POST into state. Returns True if secrets/id changed (needs persist)."""
    changed = False
    incoming = payload.get("cookies")
    if incoming is None and any(k in payload for k in COOKIE_NAMES):
        incoming = {k: payload.get(k) for k in COOKIE_NAMES}
    for name, val in _pick_cookies(incoming).items():
        if state.cookies.get(name) != val:
            state.cookies[name] = val
            changed = True

    uid = payload.get("privyUserId") or payload.get("privy_user_id")
    if isinstance(uid, str) and uid.strip() and uid.strip() != state.privy_user_id:
        state.privy_user_id = uid.strip()
        changed = True

    blob = payload.get("artefacts") if isinstance(payload.get("artefacts"), dict) else payload
    for name, val in _pick_artefacts(blob).items():
        if state.artefacts.get(name) != val:
            state.artefacts[name] = val
            changed = True

    if changed:
        state.updated_at = now
    return changed


def redact_for_log(names: Iterable[str]) -> str:
    return ",".join(sorted(set(names)))
