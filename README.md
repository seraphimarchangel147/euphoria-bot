# Euphoria Finance Bot

Automated trading bot for [Euphoria Finance](https://euphoria.finance) — a tap-trading platform on MegaETH (chain 4326).

## Architecture

Euphoria is a **server-mediated trading platform**. Trades do NOT go direct-to-contract. The flow is:

```
Client → Sign EIP-712 order → Submit to tRPC API → Server matches → Server executes on-chain
```

See [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) for the full decoded architecture.

## Status

| Component | Status | Notes |
|-----------|--------|-------|
| Price Monitor (Redstone) | ✅ Working | ETH/BTC live prices via Redstone oracle |
| Analytics | ✅ Working | Trade tracking, win rate, P&L, streaks |
| Discord Alerts | ✅ Working | Price moves >1%, trade settlements |
| EIP-712 Signing | ✅ Decoded | TakerOrder structure fully mapped |
| API Access | ✅ Via proxy | SOCKS5 proxy bypasses US geo-block |
| Auth (Identity Token) | ❌ Blocked | Need Privy identity token from browser |
| Auto-Trader | 🔨 Building | Waiting on auth to complete |

## Setup

```bash
pip install eth-account websockets aiohttp

# Set environment variables
export EUPHORIA_PRIVATE_KEY="your-megaeth-private-key"
export EUPHORIA_PRIVY_TOKEN="your-privy-identity-token"
export EUPHORIA_PROXY="socks5h://proxy:port"
```

## Usage

```bash
python3 src/monitor/price_monitor.py    # Price alerts only
python3 src/analytics/trade_tracker.py  # Track + analyze trades
python3 src/trader/auto_trader.py       # Full auto-trading (requires auth)
```

## Geo-Blocking

Euphoria blocks US IP addresses. The bot routes API calls through a SOCKS5 proxy.
Free proxies are fetched automatically from proxyscrape.com.

## Key Findings

- **Chain ID**: 4326 (MegaETH mainnet), NOT 6343 (testnet)
- **USDM Token**: `0xdf8248fee58e791149e69f6c61129D471EaFC11E`
- **Exchange Token**: `0x12759afcA690637b425ffbA3265F0Dc2F6242A8D`
- **API Base**: `https://api.mainnet.euphoria.finance/` (no `/trpc/` prefix)
- **RPC**: `https://mainnet.megaeth.com/rpc`
- **WebSocket**: `wss://api.mainnet.euphoria.finance/_events` (NATS)
- **Assets**: ETH=1, BTC=2, SOL=3
- **Amount encoding**: 18 decimals for takerAmount, 8 decimals for prices

## License

For educational purposes only. Use at your own risk.
