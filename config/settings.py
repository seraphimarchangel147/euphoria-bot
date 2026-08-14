"""Euphoria bot configuration. All secrets come from env / .env."""
from __future__ import annotations

import os
from pathlib import Path

# --- .env loading (no external dependency) ---------------------------------
def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(PROJECT_ROOT / ".env")

# --- Blockchain ------------------------------------------------------------
CHAIN_ID = 4326                       # MegaETH mainnet (6343 is TESTNET)
RPC_URL = os.environ.get("EUPHORIA_RPC_URL", "https://mainnet.megaeth.com/rpc")

# --- Contracts -------------------------------------------------------------
# Euphoria Vault (ERC-4626 wrapper). Does NOT implement EIP-2612.
USDM_ADDRESS = "0xdf8248fee58e791149e69f6c61129D471EaFC11E"
EXCHANGE_ADDRESS = "0x12759afcA690637b425ffbA3265F0Dc2F6242A8D"
MULTICALL_ADDRESS = "0xca11bde05977b3631167028862be2a173976ca11"
# Official MegaUSD — the token the frontend actually permits (EIP-2612).
# Verified on-chain: name "MegaUSD", version "1", eip712Domain + DOMAIN_SEPARATOR.
USDM_PERMIT_TOKEN = "0xFAfDdbb3FC7688494971a79cc65DCa3EF82079E7"
# Spender recovered from a live exchange permit tx (Approval spender = exchange,
# signature recovers the owner only when spender is EXCHANGE_ADDRESS).
PERMIT_SPENDER = EXCHANGE_ADDRESS
# Frontend permits type(uint256).max; deadline ~5 minutes after the tap.
PERMIT_VALUE = (1 << 256) - 1
PERMIT_DEADLINE_SECONDS = 300

# --- API -------------------------------------------------------------------
API_BASE = os.environ.get("EUPHORIA_API_BASE", "https://api.mainnet.euphoria.finance")
WS_URL = "wss://api.mainnet.euphoria.finance/_events"
ORIGIN = "https://euphoria.finance"
USER_AGENT = os.environ.get(
    "EUPHORIA_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# --- Privy auth ------------------------------------------------------------
PRIVY_APP_ID = os.environ.get("EUPHORIA_PRIVY_APP_ID", "cm9bthxvz00l9l40lc7iwyquo")
PRIVY_AUTH_BASE = "https://auth.privy.io/api/v1"
PRIVY_CLIENT = os.environ.get("EUPHORIA_PRIVY_CLIENT", "react-auth:2.13.4")
# Where refreshed tokens are cached between runs.
TOKEN_STORE = Path(
    os.environ.get("EUPHORIA_TOKEN_STORE", str(Path.home() / ".euphoria" / "tokens.json"))
)

# --- Browser session artefacts (pass-through only) -------------------------
# Captured from the user's own logged-in Euphoria tab after they solve
# Turnstile. The bot never generates, solves, or spoofs these.
# Env vars win over ~/.euphoria/session.json (see src/auth/session.py).
SESSION_STORE = Path(
    os.environ.get("EUPHORIA_SESSION_STORE", str(Path.home() / ".euphoria" / "session.json"))
)
BOT_SIGNATURE = os.environ.get("EUPHORIA_BOT_SIGNATURE", "")
DEVICE_FINGERPRINT = os.environ.get("EUPHORIA_DEVICE_FINGERPRINT", "")
BLOB = os.environ.get("EUPHORIA_BLOB", "")

# --- Proxy (geo-block bypass) ---------------------------------------------
PROXY_URL = os.environ.get("EUPHORIA_PROXY", "") or None

# --- Secrets ---------------------------------------------------------------
PRIVATE_KEY = os.environ.get("EUPHORIA_PRIVATE_KEY", "")

# --- EIP-712 ---------------------------------------------------------------
EIP712_DOMAIN = {
    "name": "Euphoria",
    "version": "1",
    "chainId": CHAIN_ID,
    "verifyingContract": EXCHANGE_ADDRESS,
}

# --- Assets ----------------------------------------------------------------
ASSETS = {"ETH": 1, "BTC": 2, "SOL": 3}
AMOUNT_DECIMALS = 18   # takerAmount
PRICE_DECIMALS = 8     # startPrice / priceInterval

# --- Oracle ----------------------------------------------------------------
REDSTONE_URL = "https://api.redstone.finance/prices"

# --- Risk limits (auto-trader safety rails) --------------------------------
MAX_TRADE_USDM = float(os.environ.get("EUPHORIA_MAX_TRADE_USDM", "10"))
MAX_DAILY_LOSS_USDM = float(os.environ.get("EUPHORIA_MAX_DAILY_LOSS_USDM", "50"))
MAX_OPEN_TRADES = int(os.environ.get("EUPHORIA_MAX_OPEN_TRADES", "3"))
DRY_RUN = os.environ.get("EUPHORIA_DRY_RUN", "1") not in ("0", "false", "False", "")
