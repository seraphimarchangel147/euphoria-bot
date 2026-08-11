# Euphoria Finance Bot

Automated trading bot for [Euphoria Finance](https://euphoria.finance) — a
tap-trading platform on MegaETH (chain 4326).

## Architecture

Euphoria is **server-mediated**. Trades do not go direct-to-contract:

```
oracle price -> risk check -> EIP-712 sign -> tRPC API -> server matches -> on-chain
```

See [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) for the decoded
protocol and [docs/AUTH.md](docs/AUTH.md) for authentication.

## Status

| Component | Status | Notes |
|---|---|---|
| Price oracle (Redstone) | Working | live ETH/BTC, staleness-guarded |
| EIP-712 signing | Working | sign + self-recover verified, 43 tests |
| Risk engine | Working | size / balance / open-count / daily-loss breaker |
| Privy auth | Working | self-renewing via refresh token (docs/AUTH.md) |
| API client | Working | tRPC query/mutate, retries, typed errors |
| Trade assembly | Working | verified end-to-end in dry run |
| Live submission | Blocked | needs botSignature (Turnstile), deviceFingerprint, approvalPermit |
| Geo-block bypass | Needs proxy | API blocks US IPs; concurrent SOCKS5 discovery included |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env      # then fill it in
python3 scripts/doctor.py # tells you exactly what is missing
```

`scripts/doctor.py` checks dependencies, wallet key, signing roundtrip, oracle,
geo-block and Privy auth, and exits non-zero if the bot is not ready.

## Usage

```bash
python3 -m src.monitor.price_monitor   # price alerts (optional Discord webhook)
python3 -m src.trader.auto_trader      # trade loop (DRY_RUN=1 by default)
python3 -m pytest                      # 43 tests
```

```python
from src.trader.auto_trader import EuphoriaTrader
trader = EuphoriaTrader()          # DRY_RUN unless EUPHORIA_DRY_RUN=0
print(trader.trade("ETH", 2.5))    # live price -> risk -> sign -> dry-run payload
```

## Safety

`EUPHORIA_DRY_RUN=1` is the default: everything runs, nothing is submitted.
Risk rails (`MAX_TRADE_USDM`, `MAX_DAILY_LOSS_USDM`, `MAX_OPEN_TRADES`) are
enforced before signing, and every order is range-checked against its solidity
types so an overflowing uint can never be signed silently.

## Geo-blocking

`api.mainnet.euphoria.finance` returns 403 to US IPs (the static frontend does
not — probe the API host, not the marketing site). Set `EUPHORIA_PROXY` to a
non-US SOCKS5 endpoint, or let `src/utils/proxy.py` discover one concurrently.

## Key findings

- Chain ID **4326** (MegaETH mainnet), not 6343 (testnet)
- USDM: `0xdf8248fee58e791149e69f6c61129D471EaFC11E`
- Exchange: `0x12759afcA690637b425ffbA3265F0Dc2F6242A8D`
- API base: `https://api.mainnet.euphoria.finance/` (no `/trpc/` prefix)
- WebSocket: `wss://api.mainnet.euphoria.finance/_events` (NATS)
- Assets: ETH=1, BTC=2, SOL=3
- `takerAmount` 18 decimals; prices 8 decimals
- `timeInterval` is **seconds** in the EIP-712 struct, **milliseconds** in the API payload

## License

Educational purposes only. Use at your own risk.
