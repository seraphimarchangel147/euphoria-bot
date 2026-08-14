"""Euphoria auto-trader: oracle -> risk -> sign -> (dry-run|submit).

Runs end-to-end in DRY_RUN today. DRY_RUN=0 additionally needs
botSignature and deviceFingerprint (plus optional blob) from a session
the user captured in their own browser — env or ~/.euphoria/session.json.
The bot does not solve Turnstile or generate fingerprints.

approvalPermit is an EIP-2612 MegaUSD permit signed here with the wallet
key. prepare() auto-attaches it when signing succeeds, and still attaches
any available session artefacts. Missing keys stay missing so
execute_trade can name them.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from config import settings
from src.auth.privy import PrivyAuth
from src.auth.session import load_session_artefacts
from src.monitor import oracle
from src.trader import eip712
from src.trader.api import EuphoriaAPI, EuphoriaAPIError, TradeRequest
from src.trader.permit import PermitError, sign_usdm_permit
from src.trader.risk import RiskEngine, RiskRejection
from src.utils.proxy import find_working_proxy

log = logging.getLogger("euphoria.trader")


@dataclass
class SignedTrade:
    request: TradeRequest
    message: dict[str, int]
    signature: str
    payload: dict[str, Any]
    signer: str


class EuphoriaTrader:
    def __init__(
        self,
        private_key: str | None = None,
        auth: PrivyAuth | None = None,
        proxy: str | None = None,
        risk: RiskEngine | None = None,
        dry_run: bool | None = None,
        discover_proxy: bool | None = None,
    ) -> None:
        self.private_key = private_key or settings.PRIVATE_KEY
        if not self.private_key:
            raise ValueError("EUPHORIA_PRIVATE_KEY is not set")
        self.address = eip712.address_for_key(self.private_key)
        self.dry_run = settings.DRY_RUN if dry_run is None else dry_run

        # Proxy resolution. `proxy=""` explicitly means "no proxy, do not scan";
        # `proxy=None` means "use the configured one, else discover".
        # Discovery is slow (network scan) so it never runs implicitly in dry run.
        should_discover = (not self.dry_run) if discover_proxy is None else discover_proxy
        if proxy is not None:
            self.proxy = proxy or None
        elif settings.PROXY_URL:
            self.proxy = settings.PROXY_URL
        elif should_discover:
            log.info("No EUPHORIA_PROXY set; scanning for a working SOCKS5 proxy...")
            self.proxy = find_working_proxy()
        else:
            self.proxy = None
        if not self.proxy and not self.dry_run:
            log.warning("No working proxy; API calls will likely be geo-blocked.")
        self.auth = auth or PrivyAuth(proxy=self.proxy)
        self.risk = risk or RiskEngine()
        self.api = EuphoriaAPI(self.auth, proxy=self.proxy)

    # -- account ------------------------------------------------------------
    def whoami(self) -> Any:
        return self.api.whoami()

    def balance(self) -> float | None:
        """Best-effort USDM balance from getGameState."""
        try:
            state = self.api.get_game_state()
        except EuphoriaAPIError as exc:
            log.warning("getGameState failed: %s", exc)
            return None
        for key in ("balance", "usdmBalance", "availableBalance"):
            if isinstance(state, dict) and key in state:
                try:
                    return float(state[key])
                except (TypeError, ValueError):
                    continue
        return None

    # -- trading ------------------------------------------------------------
    def prepare(self, req: TradeRequest, **extra: Any) -> SignedTrade:
        """Validate, risk-check and sign a trade. Never touches the network to submit."""
        self.risk.check(req.amount, balance=None)
        message = eip712.build_taker_order(
            asset=req.asset,
            amount=req.amount,
            start_time=req.start_time,
            time_interval_seconds=req.time_interval_seconds,
            start_price=req.start_price,
            price_interval=req.price_interval,
        )
        signature = eip712.sign_order(self.private_key, message)

        recovered = eip712.recover_signer(message, signature)
        if recovered.lower() != self.address.lower():
            raise RuntimeError(
                f"signature self-check failed: recovered {recovered}, expected {self.address}"
            )

        artefacts = load_session_artefacts()
        attached = [k for k, v in artefacts.as_payload().items() if k not in extra]
        for key, value in artefacts.as_payload().items():
            extra.setdefault(key, value)
        if attached:
            log.info("attached session artefacts: %s", ", ".join(attached))

        if "approvalPermit" not in extra:
            try:
                extra["approvalPermit"] = sign_usdm_permit(self.private_key).signature
                log.info("attached approvalPermit (EIP-2612 MegaUSD)")
            except PermitError as exc:
                log.warning("approvalPermit not attached: %s", exc)

        payload = self.api.to_payload(req, signature, message["nonce"], **extra)
        return SignedTrade(req, message, signature, payload, recovered)

    def submit(self, trade: SignedTrade) -> Any:
        if self.dry_run:
            log.info(
                "DRY RUN -- not submitting. %s %s USDM @ %s (nonce %s, sig %s...)",
                trade.request.asset, trade.request.amount, trade.request.start_price,
                trade.message["nonce"], trade.signature[:12],
            )
            return {"dryRun": True, "payload": trade.payload}
        result = self.api.execute_trade(trade.payload)
        self.risk.record_open()
        return result

    def trade(self, asset: str, amount: float, *, price_interval: float | None = None,
              time_interval_seconds: int = 5, **extra: Any) -> Any:
        """Fetch a live price, build, sign and submit (or dry-run) a trade."""
        quote = oracle.fetch_quote(asset)
        interval = price_interval if price_interval is not None else round(quote.price * 0.0005, 8)
        req = TradeRequest(
            asset=asset,
            amount=amount,
            start_price=quote.price,
            price_interval=interval,
            time_interval_seconds=time_interval_seconds,
            start_time=int(time.time()),
        )
        return self.submit(self.prepare(req, **extra))

    def close(self) -> None:
        self.api.close()


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        trader = EuphoriaTrader()
    except Exception as exc:
        log.error("startup failed: %s", exc)
        return 1
    log.info("wallet %s | dry_run=%s | proxy=%s", trader.address, trader.dry_run, trader.proxy)
    try:
        result = trader.trade("ETH", min(1.0, settings.MAX_TRADE_USDM))
        log.info("result: %s", result)
    except (RiskRejection, EuphoriaAPIError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return 1
    finally:
        trader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
