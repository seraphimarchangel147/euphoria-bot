# Euphoria Finance — Reverse Engineering

## Overview

Euphoria Finance is a tap-trading platform on MegaETH. Users bet on price direction within time intervals. The platform uses a grid-based UI where each cell represents a price/time combination with a multiplier.

## Architecture

### Server-Mediated Trading

Trading does NOT happen directly on-chain from the client. The flow:

1. Client constructs a `TakerOrder` with trade parameters
2. Client signs the order via EIP-712
3. Client signs a USDM spending permit (EIP-2612)
4. Client generates a "bot signature" from a registered trade key
5. Client submits everything to the server via tRPC subscription
6. Server verifies signatures, matches orders, executes on-chain

### Key Contracts

| Contract | Address | Type |
|----------|---------|------|
| USDM Token | `0xdf8248fee58e791149e69f6c61129D471EaFC11E` | ERC20 (proxy → impl) |
| Exchange Token | `0x12759afcA690637b425ffbA3265F0Dc2F6242A8D` | ERC20 (proxy → impl) |
| Multicall3 | `0xca11bde05977b3631167028862be2a173976ca11` | Standard |

Both contracts are ERC20 tokens (USDM + exchange share token). There is no direct `placeTrade()` function on any contract.

### Chain Configuration

- **Chain ID**: 4326 (MegaETH mainnet)
- **Chain ID 6343** is the MegaETH TESTNET
- **RPC**: `https://mainnet.megaeth.com/rpc`
- **Explorer**: `https://mega.etherscan.io`

## EIP-712 TakerOrder

### Domain

```javascript
{
  name: "Euphoria",
  version: "1",
  chainId: 4326,
  verifyingContract: "0x12759afcA690637b425ffbA3265F0Dc2F6242A8D"
}
```

### Types

```javascript
{
  TakerOrder: [
    { name: "takerAmount", type: "uint128" },    // 18 decimals
    { name: "underlying", type: "uint8" },        // ETH=1, BTC=2, SOL=3
    { name: "nonce", type: "uint8" },
    { name: "startTime", type: "uint64" },        // Unix timestamp (seconds)
    { name: "timeInterval", type: "uint32" },     // Duration in seconds
    { name: "startPrice", type: "uint64" },       // 8 decimals
    { name: "priceInterval", type: "uint64" }     // 8 decimals
  ]
}
```

### Asset Mapping

```javascript
const ASSETS = { ETH: 1, BTC: 2, SOL: 3 };
```

### Amount Encoding

- `takerAmount`: `amount * 10^18` (18 decimals)
- `startPrice`: `price * 10^8` (8 decimals)
- `priceInterval`: `priceInterval * 10^8` (8 decimals)

### Nonce Calculation

```javascript
function getNonce() {
  const remainder = Date.now() % 5120;
  return Math.floor(remainder / 20);
}
```

## API

### Base URL

```
https://api.mainnet.euphoria.finance/
```

**IMPORTANT**: Do NOT include `/trpc/` prefix. The tRPC procedures are at the root.

### Authentication

The API uses a Privy **identity token** (NOT the access token from localStorage).

- Header: `Authorization: Bearer <identity_token>`
- The identity token is obtained via `privy.getIdentityToken()` in the frontend
- It's stored in-memory (Zustand store), NOT in localStorage
- The access token from localStorage does NOT work as API auth

### Known Procedures

| Procedure | Method | Auth | Description |
|-----------|--------|------|-------------|
| `users.getTier` | GET | Yes | Get user tier/info |
| `users.getGameState` | GET | Yes | Get game state (balance, positions) |
| `trades.executeTrade` | Subscription | Yes | Submit a trade |

### Geo-Blocking

The API blocks US IP addresses (returns 403 with "Region Restricted" page).
Access via SOCKS5 proxy through non-US endpoints (Germany, Netherlands work).

## NATS WebSocket

### Endpoint

```
wss://api.mainnet.euphoria.finance/_events
```

### Protocol

NATS over WebSocket. Server sends `INFO` with `auth_required: true`.
Authentication requires registered credentials (not just the Privy JWT).

### Subjects

- `users.{userId}.account.wallet_deposited` — deposit events
- `quotes.{asset}_{duration}_{price}` — price quotes
- `_INBOX_{userId}.*` — request/reply

## Trade Submission Flow

From the frontend `executeTrade` function:

1. **Sign TakerOrder**: EIP-712 signature with wallet key
2. **Sign USDM Permit**: EIP-2612 permit for server to spend USDM
3. **Generate Bot Signature**: From registered trade key (requires Turnstile)
4. **Get Device Fingerprint**: Client-side browser fingerprint
5. **Encode Blob**: Encoded fingerprint data
6. **Submit via tRPC**: `trades.executeTrade.subscribe(payload)`

### Payload Structure

```javascript
{
  asset: "ETH",
  startTime: "2024-01-01T00:00:00Z",
  timeInterval: 5000,        // ms
  startPrice: 3500.50,
  priceInterval: 0.50,
  quote: 5,                  // multiplier
  amount: 10,                // USDM amount
  slippageTolerance: 0.02,
  nonce: 42,
  signature: "0x...",        // EIP-712 signature
  cellX: 3,
  cellY: 7,
  botSignature: "0x...",     // Trade key signature
  deviceFingerprint: "...",
  approvalPermit: "0x...",   // EIP-2612 permit
  quotedGridRefTime: "...",
  blob: "..."
}
```

## EIP-2612 USDM Permit

The vault token at `0xdf8248…` (Euphoria Vault / EV) is an ERC-4626 wrapper
and **does not** implement `permit()`. The token the frontend actually
permits is official MegaUSD. Verified on-chain (not assumed):

| Field | Value | How we know |
|---|---|---|
| Token | `0xFAfDdbb3FC7688494971a79cc65DCa3EF82079E7` | `eip712Domain()` + `Approval` logs |
| Domain name | `MegaUSD` | `eip712Domain()` / `name()` |
| Domain version | `1` | `eip712Domain()` |
| Chain ID | `4326` | `eip712Domain()` |
| Spender | `0x12759afcA690637b425ffbA3265F0Dc2F6242A8D` (exchange) | `Approval` spender; signature recovers the owner only for this spender |
| Value | `type(uint256).max` | calldata + `Approval` amount on a live `0x6f7e758c` exchange tx |
| Types | standard `Permit(owner, spender, value, nonce, deadline)` | EIP-2612; recover matches |
| Nonce | `MegaUSD.nonces(owner)` | was `0` before that tx, `1` after |
| Deadline | unix seconds, ~5 minutes after the tap | calldata `1786721299` vs order `startTime` `1786721005` |
| Encoding | 65-byte EIP-712 signature (`0x` + r + s + v) | same shape as `signature` |

`src/trader/permit.py` signs this permit with the wallet key. The on-chain
`DOMAIN_SEPARATOR()` is `0x26e1aa8b35bf8653b60726926f670a6ec590674f00b149826914c480d0e798bd`.

## Trade Key Registration

Trade key registration requires a **Cloudflare Turnstile** token. This is the one piece that cannot be automated server-side.

The registration sends:
- `turnstileToken`: Cloudflare challenge token
- `publicKey`: Ed25519 public key for the trade key pair
- `deviceFingerprint`: Browser fingerprint
- `userAgent`: Browser user agent
- `blob`: Encoded fingerprint data

## Frontend JS Bundles

Key files from `euphoria.finance/assets/`:

| File | Purpose |
|------|---------|
| `index-dG_9kJiZ.js` | Main app (908KB) |
| `privy-auth-EBWy2qTi.js` | Privy SDK (2.6MB) |
| `trpc-runtime-GCH1I3AT.js` | tRPC client (416KB) |
| `client-runtime-DQ-WJHC4.js` | Client runtime (85KB) |
| `trade-B23kfx9i.js` | Trade UI (79KB) |
| `auth-context-BK-EIdPX.js` | Auth context (50KB) |
| `use-sharing-dialog-DUZM_dYv.js` | Trade key mgmt (663KB, obfuscated) |
