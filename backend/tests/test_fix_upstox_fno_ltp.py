"""Owner bug 29 Oct ("Position pnl not displaying"): a PAPER option position showed "waiting for live prices" with the
market open and Upstox connected. The position is stored on exchange NFO (instruments/master.py); Upstox publishes no
NFO file (its URL answers 403) - options and futures are the NSE_FO rows of the NSE file. The adapter now reads NFO / BFO
from the NSE / BSE file, F&O segment only, so the option's price resolves for open P&L, its mark and the stop check."""
import gzip
import json

import httpx

from app.brokers.models import BrokerCredentials
from app.brokers.upstox import UpstoxBroker
from tests.test_brokers import _mock_client, run

OPTION = "NIFTY 22700 CE 04 NOV 26"
MASTER = gzip.compress(json.dumps([
    {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "segment": "NSE_EQ", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    {"instrument_key": "NSE_FO|43001", "exchange": "NSE", "segment": "NSE_FO", "trading_symbol": OPTION, "instrument_type": "CE",
     "lot_size": 75, "strike_price": 22700.0},
]).encode())


def test_an_nfo_option_is_priced_from_the_nse_file():
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "assets.upstox.com" in url:
            asked.append(url.rsplit("/", 1)[-1])
            return httpx.Response(200, content=MASTER) if url.endswith("/NSE.json.gz") else httpx.Response(403)
        assert request.url.params["instrument_key"] == "NSE_FO|43001"
        return httpx.Response(200, json={"status": "success", "data": {
            f"NSE_FO:{OPTION}": {"instrument_token": "NSE_FO|43001", "last_price": 112.35}}})

    broker = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"), client=_mock_client(handler, UpstoxBroker.BASE_URL))
    assert run(broker.get_ltp_for_symbol(OPTION, "NFO")) == 112.35
    assert asked == ["NSE.json.gz"]                                # never the NFO file Upstox does not publish
    fo = run(broker.get_instruments("NFO"))
    assert [i.instrument_token for i in fo] == ["NSE_FO|43001"]    # the F&O rows only
    assert len(run(broker.get_instruments("NSE"))) == 2            # the NSE file itself is unchanged
