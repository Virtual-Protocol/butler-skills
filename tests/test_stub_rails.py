"""test_stub_rails.py — unit tests for the rails surface stub_bevo.py added:
TradeEvent's six classification predicates against a spot buy, spot sell,
perp open, perp close, liquidation and tokenized-stock row from
fixtures/trade-activity-mixed.jsonl, and Asset.ref falling back to the symbol
when there is no address.

Loaded by file path (like tests/conftest.py loads scripts/build_index.py) so
this needs no package/sys.path setup and matches how replay.py itself loads
stub_bevo.py.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_stub_bevo():
    spec = importlib.util.spec_from_file_location("stub_bevo_under_test", REPO_ROOT / "tests" / "stub_bevo.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


stub_bevo = _load_stub_bevo()


def _events_by_id() -> dict[int, "stub_bevo.TradeEvent"]:
    path = REPO_ROOT / "tests" / "fixtures" / "trade-activity-mixed.jsonl"
    events = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            envelope = json.loads(line)
            event = stub_bevo._parse_trade_event(envelope["event"])
            events[event.id] = event
    return events


EVENTS = _events_by_id()


def test_spot_buy_classifies_as_buy_only():
    trade = EVENTS[101]
    assert trade.is_buy is True
    assert trade.is_sell is False
    assert trade.is_perp is False
    assert trade.is_long is False
    assert trade.is_short is False
    assert trade.is_close is False


def test_spot_sell_classifies_as_sell_only():
    trade = EVENTS[102]
    assert trade.is_buy is False
    assert trade.is_sell is True
    assert trade.is_perp is False
    assert trade.is_long is False
    assert trade.is_short is False
    assert trade.is_close is False


def test_perp_open_classifies_as_long_not_buy_or_close():
    trade = EVENTS[103]
    assert trade.is_buy is False  # spot-only
    assert trade.is_sell is False  # spot-only
    assert trade.is_perp is True
    assert trade.is_long is True
    assert trade.is_short is False
    assert trade.is_close is False


def test_perp_close_classifies_via_reduce_only():
    trade = EVENTS[104]
    assert trade.is_buy is False
    assert trade.is_sell is False
    assert trade.is_perp is True
    assert trade.is_close is True


def test_liquidation_classifies_as_close_via_hl_event():
    trade = EVENTS[105]
    assert trade.is_buy is False
    assert trade.is_sell is False
    assert trade.is_perp is True
    assert trade.is_close is True


def test_stock_row_classifies_as_buy_like_any_spot_row():
    trade = EVENTS[106]
    assert trade.is_buy is True
    assert trade.is_sell is False
    assert trade.is_perp is False
    assert trade.is_close is False
    assert stub_bevo.is_stock(trade.token) is True


def test_asset_ref_falls_back_to_symbol_when_there_is_no_address():
    asset = stub_bevo.Asset(symbol="AAPL", address=None, chain_id=8453)
    assert asset.address is None
    assert asset.ref == "AAPL"
    assert str(asset) == "AAPL@8453"
