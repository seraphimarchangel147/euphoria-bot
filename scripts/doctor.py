#!/usr/bin/env python3
"""Preflight check: tells you exactly which piece of the bot is not ready.

    python3 scripts/doctor.py

Checks dependencies, wallet, signing, oracle, geo-block, Privy cookie
auth (users.getProfile), USDM permit readiness, and whether captured
session artefacts are present — it never invents them or prints secrets.
Exits non-zero if anything required for live trading is missing.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402

OK, WARN, FAIL = "[ ok ]", "[warn]", "[FAIL]"
_failed = False


def report(status: str, name: str, detail: str = "") -> None:
    global _failed
    if status == FAIL:
        _failed = True
    print(f"{status} {name}" + (f" -- {detail}" if detail else ""))


def check_deps() -> None:
    try:
        import httpx  # noqa: F401
        report(OK, "httpx installed")
    except ImportError:
        report(FAIL, "httpx missing", "pip install 'httpx[socks]'")
    try:
        __import__("eth" + "_account")
        report(OK, "eth-account installed")
    except ImportError:
        report(FAIL, "eth-account missing", "pip install 'eth-account>=0.13'")


def check_wallet() -> None:
    if not settings.PRIVATE_KEY:
        report(FAIL, "EUPHORIA_PRIVATE_KEY", "not set (see .env.example)")
        return
    try:
        from src.trader import eip712
        addr = eip712.address_for_key(settings.PRIVATE_KEY)
        report(OK, "wallet key valid", addr)
    except Exception as exc:
        report(FAIL, "wallet key invalid", str(exc))


def check_signing() -> None:
    try:
        from src.trader import eip712
        key = "0x" + "11" * 32
        order = eip712.build_taker_order("ETH", 1, int(time.time()), 5, 3000.0, 0.5, nonce=1)
        sig = eip712.sign_order(key, order)
        rec = eip712.recover_signer(order, sig)
        assert rec.lower() == eip712.address_for_key(key).lower()
        report(OK, "EIP-712 sign+recover roundtrip", f"sig {sig[:14]}...")
    except Exception as exc:
        report(FAIL, "EIP-712 signing broken", str(exc))


def check_oracle() -> None:
    try:
        from src.monitor import oracle
        q = oracle.fetch_quote("ETH")
        report(OK, "Redstone oracle", f"ETH ${q.price:,.2f} ({q.age:.0f}s old)")
    except Exception as exc:
        report(FAIL, "Redstone oracle unreachable", str(exc))


def check_geo() -> None:
    """Probe the API host, not the marketing site.

    euphoria.finance (static frontend) serves 200 from anywhere; it is
    api.mainnet.euphoria.finance that enforces the region block. Probing the
    wrong host produces a false "reachable".
    """
    import httpx
    url = settings.API_BASE + "/users.getTier"
    try:
        resp = httpx.get(url, timeout=20.0,
                         headers={"User-Agent": settings.USER_AGENT,
                                  "Origin": settings.ORIGIN},
                         proxy=settings.PROXY_URL, follow_redirects=True)
    except Exception as exc:
        report(FAIL, "API host unreachable", str(exc))
        return

    body = resp.text[:500]
    # 403/451 from the API edge, or an explicit region page, means blocked.
    # 401 means we reached the app and only lack auth -- geo is fine.
    geo_blocked = resp.status_code in (403, 451) or "Region Restricted" in body
    if geo_blocked:
        where = f"via {settings.PROXY_URL}" if settings.PROXY_URL else "from this IP (no proxy set)"
        report(FAIL, "API is GEO-BLOCKED", f"{resp.status_code} {where}; set EUPHORIA_PROXY to a non-US SOCKS5 proxy")
    elif resp.status_code == 401:
        # Reached the app layer: geo is fine, we simply are not authenticated yet.
        report(OK, "API reachable (401 = geo OK, auth pending)", settings.PROXY_URL or "direct")
    else:
        report(OK, f"API reachable ({resp.status_code})", settings.PROXY_URL or "direct")


def check_auth() -> None:
    from src.auth.privy import PrivyAuth, PrivyAuthError
    try:
        auth = PrivyAuth()
    except PrivyAuthError as exc:
        report(FAIL, "Privy credentials", str(exc).split(".")[0])
        return
    try:
        status = auth.cookie_status()
    except PrivyAuthError as exc:
        report(FAIL, "API cookies", str(exc)[:160])
        return
    if status:
        detail = ", ".join(f"{name} {n} chars" for name, n in status)
        report(OK, "API cookies", detail)
    else:
        report(FAIL, "API cookies", "privy-id-token missing — see docs/AUTH.md")
        return
    uid = auth.privy_user_id()
    if uid:
        shown = uid[:22] + "..." if len(uid) > 22 else uid
        report(OK, "privyUserId", shown)
    else:
        report(WARN, "privyUserId", "not set — users.getProfile needs {privyUserId}")
    try:
        from src.trader.api import EuphoriaAPI
        api = EuphoriaAPI(auth)
        try:
            api.whoami()
        finally:
            api.close()
        report(OK, "users.getProfile", "authenticated")
    except Exception as exc:
        report(FAIL, "users.getProfile", str(exc)[:160])


def check_session() -> None:
    """Report captured Turnstile / fingerprint artefacts without inventing them."""
    from src.auth.session import load_session_artefacts

    arts = load_session_artefacts()
    required = (("botSignature", True), ("deviceFingerprint", True), ("blob", False))
    for key, needed in required:
        present = bool(getattr(arts, key))
        source = arts.sources.get(key, "")
        if present:
            n = len(getattr(arts, key))
            where = f"from {source}, {n} chars" if source else f"{n} chars"
            report(OK, f"session {key}", where)
            continue
        if needed:
            status = FAIL if not settings.DRY_RUN else WARN
            report(
                status,
                f"session {key}",
                "not captured — paste from your own Euphoria tab (docs/AUTH.md)",
            )
        else:
            report(WARN, f"session {key}", "optional; not set")


def check_permit() -> None:
    """Key present + can sign an EIP-2612 permit. Never prints the full signature."""
    if not settings.PRIVATE_KEY:
        report(WARN, "USDM permit", "no wallet key — cannot sign approvalPermit")
        return
    try:
        from src.trader.permit import recover_permit_signer, sign_usdm_permit
        signed = sign_usdm_permit(settings.PRIVATE_KEY, nonce=0, deadline=(1 << 256) - 1)
        rec = recover_permit_signer(signed.message, signed.signature)
        if rec.lower() != signed.owner.lower():
            report(FAIL, "USDM permit recover mismatch", rec)
            return
        report(
            OK,
            "USDM EIP-2612 permit",
            f"spender exchange, sig {signed.signature[:14]}...",
        )
    except Exception as exc:
        report(FAIL, "USDM permit signing broken", str(exc)[:160])


def check_mode() -> None:
    if settings.DRY_RUN:
        report(OK, "DRY_RUN enabled", "no orders will be submitted")
    else:
        report(WARN, "DRY_RUN DISABLED", f"live orders up to {settings.MAX_TRADE_USDM} USDM")


def main() -> int:
    print("== Euphoria bot preflight ==")
    check_deps()
    check_signing()
    check_wallet()
    check_oracle()
    check_geo()
    check_auth()
    check_session()
    check_permit()
    check_mode()
    print()
    if _failed:
        print("Not ready. Fix the [FAIL] lines above.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
