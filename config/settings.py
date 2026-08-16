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
USDM_ADDRESS = "0xdf8248fee58e791149e69f6c61129D471EaFC11E"
EXCHANGE_ADDRESS = "0x12759afcA690637b425ffbA3265F0Dc2F6242A8D"
MULTICALL_ADDRESS = "0xca11bde05977b3631167028862be2a173976ca11"

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
# Per-quote cell-edge tape. Empty / missing file = empty ledger.
# Historical reachability (d, h) rows cannot be imported. Clock starts empty.
CELL_EDGE_STORE = Path(
    os.environ.get("EUPHORIA_CELL_EDGE_STORE", str(Path.home() / ".euphoria" / "cell_edge.json"))
)

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
