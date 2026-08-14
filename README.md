# Euphoria Finance Bot

Automated trading bot for [Euphoria Finance](https://euphoria.finance) — a
tap-trading platform on MegaETH (chain 4326).

## Architecture

Euphoria is **server-mediated**. Trades do not go direct-to-contract:

```
ticks (extension or Redstone) -> think signal -> risk -> EIP-712 sign -> tRPC API
```

The Python process still does **not** speak the authenticated ~10Hz Euphoria
WebSocket. Redstone HTTP is the fallback. Live page ticks arrive only if you
load the Chrome extension on a logged-in tab.

See [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) for the decoded
protocol and [docs/AUTH.md](docs/AUTH.md) for authentication.

## Status

| Component | Status | Notes |
|---|---|---|
| Control room | Working | `python -m src.ui` — localhost dashboard + start/stop/think |
| Think signal | Working | nearby 5s tap + 1m/5m/1h/4h/D/M bias (`src/analytics/signal.py`) |
| Chrome extension | Working | helper overlay in a logged-in tab: prices, session, /trade |
| Price oracle (Redstone) | Working | fallback when extension ticks are stale |
| EIP-712 signing | Working | sign + self-recover verified |
| Risk engine | Working | size / balance / open-count / daily-loss breaker |
| Privy auth | Working | refresh token (existing) + cookies via extension/env |
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
python3 -m src.ui                      # operator control room (127.0.0.1:8765)
python3 -m src.control                 # same server
python3 -m src.monitor.price_monitor   # price alerts (optional Discord webhook)
python3 -m src.trader.auto_trader      # one-shot trade loop (DRY_RUN=1 by default)
python3 -m pytest
```

```python
from src.trader.auto_trader import EuphoriaTrader
trader = EuphoriaTrader()          # DRY_RUN unless EUPHORIA_DRY_RUN=0
print(trader.trade("ETH", 2.5))    # live price -> risk -> sign -> dry-run payload
```

## Control room

```bash
python3 -m src.ui
```

Binds **127.0.0.1 only** (default port `8765`, override with
`EUPHORIA_CONTROL_PORT`). Open http://127.0.0.1:8765/

| Method | Path | What it does |
|---|---|---|
| GET | `/status` | running/stopped, mode, dry_run, last quotes, last decision, last error, extension link |
| GET | `/think` | current signal: nearby 5s pick/candidates, higher-TF stack (1m/5m/1h/4h/D/M), reason |
| GET | `/events` | SSE snapshot (or `?after=&wait=` long-poll) — same payload the overlay paints |
| POST | `/start` `/stop` | operator run switch |
| POST | `/mode` | `{"mode":"manual"}` or `{"mode":"auto"}` |
| POST | `/session` | extension posts `{cookies, privyUserId, quotes?}` |

**Manual** only publishes think / overlay. It never submits.

**Auto** still will not live-submit unless `EUPHORIA_DRY_RUN=0` **and**
`botSignature`, `deviceFingerprint`, and `approvalPermit` are present. Dry-run
stays the default.

The dashboard and the `/trade` overlay are one control room. Start, stop, and
mode on either side hit the same `ControlRoom`. `/events` pushes the shared
snapshot so a click on one surface shows on the other within a beat. The
dashboard shows whether the trade-tab helper is connected and whether ticks
are **from tab** (not only Redstone). Manual never submits — you tap.

Think names the **nearest square** the 5s tape is likely to *touch once*
(official rule: price only has to enter the zone). Separately it reads a
higher-timeframe stack — 1 minute, 5 minute, 1 hour, 4 hour, daily, monthly —
from public OHLC when available, or from accumulated ticks for the short
windows. Higher TFs are bias only: a nearby tap can be with-trend or fading.
Choppy or quiet tape → `no trade`. Far lottery cells are never named.

## Chrome extension

Load unpacked from [`extension/`](extension/README.md):

1. Stay in a normal Chrome profile already logged into Euphoria.
2. Start the local helper (`python -m src.ui`).
3. `chrome://extensions` → Developer mode → Load unpacked → `extension/`.
4. Pin it. Open `/trade`.

The overlay sits on the live `/trade` canvas: pink on nearby candidate tiles,
blue on the selected pick, plus looking-at, the 1m/5m/1h/4h/D/M lean, a short
why, and last-window hit/miss. A small card still shows start/stop. Manual
mode is an indicator — you tap. Details in [docs/AUTH.md](docs/AUTH.md).

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
