"""EIP-712 TakerOrder signing for Euphoria Finance."""
import time
from eth_account import Account
from eth_account.messages import encode_typed_data


EIP712_DOMAIN = {
    "name": "Euphoria",
    "version": "1",
    "chainId": 4326,
    "verifyingContract": "0x12759afcA690637b425ffbA3265F0Dc2F6242A8D",
}

TAKER_ORDER_TYPES = {
    "TakerOrder": [
        {"name": "takerAmount", "type": "uint128"},
        {"name": "underlying", "type": "uint8"},
        {"name": "nonce", "type": "uint8"},
        {"name": "startTime", "type": "uint64"},
        {"name": "timeInterval", "type": "uint32"},
        {"name": "startPrice", "type": "uint64"},
        {"name": "priceInterval", "type": "uint64"},
    ]
}

ASSETS = {"ETH": 1, "BTC": 2, "SOL": 3}


def get_nonce() -> int:
    """Generate the nonce the same way the frontend does."""
    return (int(time.time() * 1000) % 5120) // 20


def encode_amount(amount: float) -> int:
    """Encode USDM amount to uint128 (18 decimals)."""
    return int(amount * 10**18)


def encode_price(price: float) -> int:
    """Encode price to uint64 (8 decimals)."""
    return int(price * 10**8)


def build_taker_order(
    asset: str,
    amount: float,
    start_time: int,
    time_interval: int,
    start_price: float,
    price_interval: float,
) -> dict:
    """Build the EIP-712 message for a TakerOrder."""
    underlying = ASSETS.get(asset.upper())
    if underlying is None:
        raise ValueError(f"Unknown asset: {asset}. Supported: {list(ASSETS.keys())}")

    return {
        "takerAmount": encode_amount(amount),
        "underlying": underlying,
        "nonce": get_nonce(),
        "startTime": start_time,
        "timeInterval": time_interval,
        "startPrice": encode_price(start_price),
        "priceInterval": encode_price(price_interval),
    }


def sign_order(private_key: str, message: dict) -> str:
    """Sign a TakerOrder with EIP-712. Returns hex signature."""
    typed_data = {
        "domain": EIP712_DOMAIN,
        "types": {**TAKER_ORDER_TYPES, "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ]},
        "primaryType": "TakerOrder",
        "message": message,
    }
    encoded = encode_typed_data(full_message=typed_data)
    signed = Account.sign_message(encoded, private_key)
    return signed.signature.hex()
