"""Risk rails. Nothing reaches the exchange without clearing these."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import settings


class RiskRejection(RuntimeError):
    """Raised when a proposed trade violates a configured limit."""


@dataclass
class RiskState:
    realised_pnl_today: float = 0.0
    open_trades: int = 0
    day_stamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))

    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self.day_stamp:
            self.day_stamp = today
            self.realised_pnl_today = 0.0


@dataclass
class RiskEngine:
    max_trade: float = settings.MAX_TRADE_USDM
    max_daily_loss: float = settings.MAX_DAILY_LOSS_USDM
    max_open: int = settings.MAX_OPEN_TRADES
    state: RiskState = field(default_factory=RiskState)

    def check(self, amount: float, balance: float | None = None) -> None:
        """Raise RiskRejection if this trade must not be placed."""
        self.state._roll_day()
        if amount <= 0:
            raise RiskRejection(f"trade amount {amount} must be positive")
        if amount > self.max_trade:
            raise RiskRejection(
                f"trade size {amount} USDM exceeds MAX_TRADE_USDM={self.max_trade}"
            )
        if balance is not None and amount > balance:
            raise RiskRejection(f"trade size {amount} exceeds balance {balance}")
        if self.state.open_trades >= self.max_open:
            raise RiskRejection(
                f"{self.state.open_trades} open trades >= MAX_OPEN_TRADES={self.max_open}"
            )
        if -self.state.realised_pnl_today >= self.max_daily_loss:
            raise RiskRejection(
                f"daily loss {-self.state.realised_pnl_today:.2f} USDM has hit the "
                f"MAX_DAILY_LOSS_USDM={self.max_daily_loss} circuit breaker"
            )

    def record_open(self) -> None:
        self.state.open_trades += 1

    def record_settled(self, pnl: float) -> None:
        self.state.open_trades = max(0, self.state.open_trades - 1)
        self.state._roll_day()
        self.state.realised_pnl_today += pnl
