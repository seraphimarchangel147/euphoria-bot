#!/usr/bin/env python3
"""Write captured session artefacts to ~/.euphoria/session.json (mode 0600).

    python3 scripts/save_session.py < artefacts.json

Reads JSON from stdin (the object the DevTools snippet copies). Stores only
botSignature / deviceFingerprint / blob. Never stores private keys.

This is pass-through only — capture the values in YOUR own logged-in
Euphoria tab after YOU solve Turnstile. See docs/AUTH.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth.session import save_session_artefacts  # noqa: E402


def main() -> int:
    if sys.stdin.isatty():
        print("Paste the JSON object copied from the Euphoria tab, then Ctrl-D.", file=sys.stderr)
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("expected a JSON object with botSignature / deviceFingerprint / blob", file=sys.stderr)
        return 1
    path = save_session_artefacts(data)
    saved = json.loads(path.read_text())
    present = [k for k in ("botSignature", "deviceFingerprint", "blob") if saved.get(k)]
    print(f"wrote {path} (0600): {', '.join(present) or 'no artefact keys'}")
    return 0 if present else 1


if __name__ == "__main__":
    raise SystemExit(main())
