"""Euphoria bot configuration."""
import os

# --- Blockchain ---
CHAIN_ID = 4326  # MegaETH mainnet (NOT 6343 which is testnet)
RPC_URL = "https://mainnet.megaeth.com/rpc"

# --- Contracts ---
USDM_ADDRESS = "0xdf8248fee58e791149e69f6c61129D471EaFC11E"
EXCHANGE_ADDRESS = "0x12759afcA690637b425ffbA3265F0Dc2F6242A8D"
MULTICALL_ADDRESS = "0xca11bde05977b3631167028862be2a173976ca11"

# --- API ---
API_BASE = "https://api.mainnet.euphoria.finance"
WS_URL = "wss://api.mainnet.euphoria.finance/_events"
PRIVY_APP_ID = "cm9bthxvz00l9l40lc7iwyquo"

# --- Proxy (geo-block bypass) ---
PROXY_URL = os.environ.get("EUPHORIA_PROXY", "")

# --- Auth ---
PRIVATE_KEY = os.environ.get("EUPHORIA_PRIVATE_KEY", "")
PRIVY_TOKEN = os.environ.get("EUPHORIA_PRIVY_TOKEN", "")

# --- EIP-712 Domain ---
EIP712_DOMAIN = {
    "name": "Euphoria",
    "version": "1",
    "chainId": CHAIN_ID,
    "verifyingContract": EXCHANGE_ADDRESS,
}

# --- Assets ---
ASSETS = {"ETH": 1, "BTC": 2, "SOL": 3}
ASSET_DECIMALS = 18  # takerAmount uses 18 decimals
PRICE_DECIMALS = 8   # prices use 8 decimals

# --- Redstone Oracle ---
REDSTONE_ETH_URL = "https://api.redstone.finance/prices?symbol=ETH&provider=redstone"
REDSTONE_BTC_URL = "https://api.redstone.finance/prices?symbol=BTC&provider=redstone"
