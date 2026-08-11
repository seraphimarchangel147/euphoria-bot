import json

import pytest

from src.trader.api import EuphoriaAPI, EuphoriaAPIError, TradeRequest


class FakeResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_unwrap_extracts_trpc_result_data():
    assert EuphoriaAPI._unwrap("x", FakeResp(body={"result": {"data": {"tier": 3}}})) == {"tier": 3}


def test_unwrap_passes_through_plain_body():
    assert EuphoriaAPI._unwrap("x", FakeResp(body={"tier": 3})) == {"tier": 3}


def test_unwrap_raises_on_error_body():
    with pytest.raises(EuphoriaAPIError):
        EuphoriaAPI._unwrap("x", FakeResp(body={"error": {"message": "nope"}}))


def test_unwrap_raises_on_http_error():
    with pytest.raises(EuphoriaAPIError):
        EuphoriaAPI._unwrap("x", FakeResp(status=500, body={"a": 1}))


def test_unwrap_raises_on_non_json():
    with pytest.raises(EuphoriaAPIError):
        EuphoriaAPI._unwrap("x", FakeResp(body=None, text="<html>503</html>"))


def test_payload_converts_seconds_to_milliseconds():
    req = TradeRequest(asset="eth", amount=2, start_price=3000.0,
                       price_interval=0.5, time_interval_seconds=5, start_time=1700000000)
    payload = EuphoriaAPI.to_payload(req, "0xsig", 42)
    assert payload["timeInterval"] == 5000
    assert payload["asset"] == "ETH"
    assert payload["startTime"] == "2023-11-14T22:13:20Z"
    assert payload["nonce"] == 42


def test_execute_trade_reports_missing_artefacts():
    api = EuphoriaAPI.__new__(EuphoriaAPI)   # no network
    with pytest.raises(EuphoriaAPIError) as exc:
        api.execute_trade({"signature": "0xsig"})
    for field in ("botSignature", "deviceFingerprint", "approvalPermit"):
        assert field in str(exc.value)
