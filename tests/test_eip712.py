import time

import pytest

from src.trader import eip712
from src.trader.eip712 import OrderValidationError

# Well-known throwaway key. Never used for real funds.
KEY = "0x" + "11" * 32


def base_order(**over):
    kw = dict(
        asset="ETH", amount=1.5, start_time=1700000000,
        time_interval_seconds=5, start_price=3500.5, price_interval=0.5,
        nonce=7,
    )
    kw.update(over)
    return eip712.build_taker_order(**kw)


def test_amount_encoding_is_exact():
    assert eip712.encode_amount(1) == 10 ** 18
    assert eip712.encode_amount(0.1) == 10 ** 17
    # float(0.1+0.2) style drift must not leak a wei
    assert eip712.encode_amount("0.3") == 3 * 10 ** 17


def test_price_encoding_truncates_dust_not_rounds_up():
    assert eip712.encode_price(3500.5) == 350050000000
    assert eip712.encode_price("0.000000019") == 1


def test_nonce_fits_uint8():
    for ms in (0, 1, 5119, 123456789, int(time.time() * 1000)):
        assert 0 <= eip712.get_nonce(ms) <= 255


def test_build_order_shape():
    order = base_order()
    assert order["underlying"] == 1
    assert order["takerAmount"] == 15 * 10 ** 17
    assert order["startPrice"] == 350050000000
    assert set(order) == {f["name"] for f in eip712.TAKER_ORDER_TYPES["TakerOrder"]}


def test_unknown_asset_rejected():
    with pytest.raises(OrderValidationError):
        base_order(asset="DOGE")


def test_zero_amount_rejected():
    with pytest.raises(OrderValidationError):
        base_order(amount=0)


def test_zero_price_rejected():
    with pytest.raises(OrderValidationError):
        base_order(start_price=0)


def test_uint_overflow_rejected():
    with pytest.raises(OrderValidationError):
        base_order(nonce=256)                       # uint8
    with pytest.raises(OrderValidationError):
        base_order(time_interval_seconds=2 ** 32)   # uint32
    with pytest.raises(OrderValidationError):
        base_order(amount=2 ** 128)                 # uint128


def test_negative_amount_rejected():
    with pytest.raises(OrderValidationError):
        base_order(amount=-1)


def test_signature_is_0x_prefixed_65_bytes():
    sig = eip712.sign_order(KEY, base_order())
    assert sig.startswith("0x")
    assert len(sig) == 132
    assert sig[2:] == sig[2:].lower()
    int(sig, 16)   # valid hex


def test_signature_roundtrips_to_signer():
    order = base_order()
    sig = eip712.sign_order(KEY, order)
    assert eip712.recover_signer(order, sig).lower() == eip712.address_for_key(KEY).lower()


def test_signature_is_deterministic_and_field_sensitive():
    o1, o2 = base_order(), base_order()
    assert eip712.sign_order(KEY, o1) == eip712.sign_order(KEY, o2)
    assert eip712.sign_order(KEY, base_order(amount=1.6)) != eip712.sign_order(KEY, o1)


def test_domain_pins_mainnet_chain():
    assert eip712.EIP712_DOMAIN["chainId"] == 4326      # not 6343 (testnet)
