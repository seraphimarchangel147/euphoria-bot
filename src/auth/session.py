"""Pass-through store for browser-captured trade-session artefacts.

The bot never solves Cloudflare Turnstile, never mints a trade-key
signature, and never generates or spoofs a device fingerprint. Those
values exist only after the user completes the challenge in their own
logged-in Euphoria tab and pastes the result here (see docs/AUTH.md).

Precedence: environment variables win over ~/.euphoria/session.json.
Empty / missing values stay missing so execute_trade can name them.
Private keys are never read from or written to this file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from config import settings

# Payload keys the frontend puts on executeTrade. blob is optional.
ARTEFACT_KEYS: tuple[str, ...] = ("botSignature", "deviceFingerprint", "blob")

_ENV_FOR_KEY: dict[str, str] = {
    "botSignature": "EUPHORIA_BOT_SIGNATURE",
    "deviceFingerprint": "EUPHORIA_DEVICE_FINGERPRINT",
    "blob": "EUPHORIA_BLOB",
}

_SETTINGS_ATTR: dict[str, str] = {
    "botSignature": "BOT_SIGNATURE",
    "deviceFingerprint": "DEVICE_FINGERPRINT",
    "blob": "BLOB",
}

# Accept snake_case / env-style aliases when reading a hand-written file.
_ALIASES: dict[str, str] = {
    "botSignature": "botSignature",
    "bot_signature": "botSignature",
    "EUPHORIA_BOT_SIGNATURE": "botSignature",
    "deviceFingerprint": "deviceFingerprint",
    "device_fingerprint": "deviceFingerprint",
    "EUPHORIA_DEVICE_FINGERPRINT": "deviceFingerprint",
    "blob": "blob",
    "EUPHORIA_BLOB": "blob",
}

# Never persist credentials in session.json — this store is artefacts only.
_FORBIDDEN: frozenset[str] = frozenset(
    {
        "private_key",
        "privateKey",
        "privatekey",
        "EUPHORIA_PRIVATE_KEY",
        "mnemonic",
        "seed",
        "refresh_token",
        "refreshToken",
        "identity_token",
        "identityToken",
        "access_token",
        "accessToken",
    }
)


@dataclass(frozen=True)
class SessionArtefacts:
    """Captured executeTrade extras. Absent fields are empty strings."""

    botSignature: str = ""
    deviceFingerprint: str = ""
    blob: str = ""
    sources: dict[str, str] = field(default_factory=dict)

    def as_payload(self) -> dict[str, str]:
        """Only keys that actually have a value — never invents missing ones."""
        return {k: v for k, v in (
            ("botSignature", self.botSignature),
            ("deviceFingerprint", self.deviceFingerprint),
            ("blob", self.blob),
        ) if v}


def resolve_store_path(store_path: Path | None = None) -> Path:
    if store_path is not None:
        return Path(store_path)
    env = (os.environ.get("EUPHORIA_SESSION_STORE") or "").strip()
    if env:
        return Path(env)
    return Path(settings.SESSION_STORE)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_forbidden(key: str) -> bool:
    return key in _FORBIDDEN or key.lower() in {k.lower() for k in _FORBIDDEN}


def _artefacts_from_mapping(data: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_key, raw_val in data.items():
        if _is_forbidden(str(raw_key)):
            continue
        key = _ALIASES.get(str(raw_key))
        if key is None or key not in ARTEFACT_KEYS:
            continue
        val = _clean(raw_val)
        if val:
            out[key] = val
    return out


def _read_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return _artefacts_from_mapping(data)


def _from_env(key: str) -> str:
    return _clean(os.environ.get(_ENV_FOR_KEY[key], ""))


def _from_settings(key: str) -> str:
    return _clean(getattr(settings, _SETTINGS_ATTR[key], ""))


def load_session_artefacts(*, store_path: Path | None = None) -> SessionArtefacts:
    """Load artefacts. Env wins, then settings snapshot, then session.json.

    Missing keys stay empty. Nothing is generated.
    """
    file_vals = _read_file(resolve_store_path(store_path))
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for key in ARTEFACT_KEYS:
        env_val = _from_env(key)
        settings_val = _from_settings(key)
        file_val = file_vals.get(key, "")
        if env_val:
            values[key] = env_val
            sources[key] = "env"
        elif settings_val:
            values[key] = settings_val
            sources[key] = "settings"
        elif file_val:
            values[key] = file_val
            sources[key] = "file"
    return SessionArtefacts(
        botSignature=values.get("botSignature", ""),
        deviceFingerprint=values.get("deviceFingerprint", ""),
        blob=values.get("blob", ""),
        sources=sources,
    )


def save_session_artefacts(
    data: Mapping[str, Any],
    *,
    store_path: Path | None = None,
) -> Path:
    """Write artefact keys only (0600). Strips private keys and other secrets."""
    path = resolve_store_path(store_path)
    existing = _read_file(path)
    incoming = _artefacts_from_mapping(data)
    out = {k: v for k, v in existing.items() if k in ARTEFACT_KEYS and v}
    out.update(incoming)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path

