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
# Extension / env cookie session (mode 0600). Separate from the Privy refresh bundle.
SESSION_STORE = Path(
    os.environ.get("EUPHORIA_SESSION_STORE", str(Path.home() / ".euphoria" / "session.json"))
)

# --- Operator control room (localhost only) --------------------------------
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = int(os.environ.get("EUPHORIA_CONTROL_PORT", "8765"))

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

# --- Learned calibration ---------------------------------------------------
# Beta-posterior touch-probability buckets, persisted between runs.
CALIBRATION_STORE = Path(
    os.environ.get("EUPHORIA_CALIBRATION_STORE", str(Path.home() / ".euphoria" / "calibration.json"))
)
# Paper bankroll the policy sizes against until the API hands us a real balance.
BANKROLL_START = float(os.environ.get("EUPHORIA_BANKROLL_START", "100"))
# Equity curve + settled-tap ledger, so a restart does not erase the track record.
# Sigma anchored to the house quote grid. DEFAULT OFF: measured harmful.
# Matched 50-minute windows, identical code, one setting apart --
#   anchor ON : 1218 trades, 15.5% hit, -0.7358 per unit
#   anchor OFF:  772 trades, 64.0% hit, -0.1066 per unit
# A 0.629 gap at 9.3 sigma. Off is also the first result in this project to
# beat picking at random (-0.18). The anchor ran sigma ~3.4x above the tick
# fit, which made far cells look reachable, and the book took them.
USE_HOUSE_ANCHOR = os.environ.get("EUPHORIA_HOUSE_ANCHOR", "0") not in ("0", "false", "no")
REACHABILITY_STORE = Path(
    os.environ.get("EUPHORIA_REACHABILITY_STORE",
                   str(Path.home() / ".euphoria" / "reachability.json"))
)
CELL_EDGE_STORE = Path(
    os.environ.get("EUPHORIA_CELL_EDGE_STORE",
                   str(Path.home() / ".euphoria" / "cell_edge.json"))
)
BANKROLL_STORE = Path(
    os.environ.get("EUPHORIA_BANKROLL_STORE", str(Path.home() / ".euphoria" / "bankroll.json"))
)
# Learned grid-walking behaviour: dwell per row, break direction, hour-of-day.
TRAVERSAL_STORE = Path(
    os.environ.get("EUPHORIA_TRAVERSAL_STORE", str(Path.home() / ".euphoria" / "traversal.json"))
)
# Settled-trade ledger with attribution, plus the real balance history.
PNL_STORE = Path(
    os.environ.get("EUPHORIA_PNL_STORE", str(Path.home() / ".euphoria" / "pnl.json"))
)
# The operator's own real taps and settlements, and what our surface said about
# each. A record of real bets -- written 0600, never logged.
PLAYER_STORE = Path(
    os.environ.get("EUPHORIA_PLAYER_STORE", str(Path.home() / ".euphoria" / "player.json"))
)
# Calibration learned from real money only, kept apart from the free labels.
PLAYER_CALIBRATION_STORE = Path(
    os.environ.get("EUPHORIA_PLAYER_CALIBRATION_STORE",
                   str(Path.home() / ".euphoria" / "player-calibration.json"))
)
# Minimum expected value, on the lower confidence bound, before a cell is tapped.
MIN_EDGE = float(os.environ.get("EUPHORIA_MIN_EDGE", "0.05"))
# Fraction of the Kelly stake actually used. Full Kelly on an estimated
# probability is how a real edge still ends in ruin.
KELLY_FRACTION = float(os.environ.get("EUPHORIA_KELLY_FRACTION", "0.25"))

# --- Risk limits (auto-trader safety rails) --------------------------------
MAX_TRADE_USDM = float(os.environ.get("EUPHORIA_MAX_TRADE_USDM", "10"))
MAX_DAILY_LOSS_USDM = float(os.environ.get("EUPHORIA_MAX_DAILY_LOSS_USDM", "50"))
MAX_OPEN_TRADES = int(os.environ.get("EUPHORIA_MAX_OPEN_TRADES", "3"))
DRY_RUN = os.environ.get("EUPHORIA_DRY_RUN", "1") not in ("0", "false", "False", "")
