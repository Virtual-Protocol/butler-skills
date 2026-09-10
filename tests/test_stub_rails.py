"""test_stub_rails.py — unit tests for the rails surface stub_bevo.py added:
TradeEvent's six classification predicates against a spot buy, spot sell,
perp open, perp close, liquidation and tokenized-stock row from
fixtures/trade-activity-mixed.jsonl, and Asset.ref falling back to the symbol
when there is no address.

Then the money rail itself: `trade(command=…)`'s grammar gate (one test per
refusal the real duty shim makes), the `/user-assets` fixture's own invariants
(stock shares apart from the look-alike token row, a HIP-3 namespaced perp),
`holdings()` over that same fixture, and `state` on disk in the replay's state
directory.

Loaded by file path (like tests/conftest.py loads scripts/build_index.py) so
this needs no package/sys.path setup and matches how replay.py itself loads
stub_bevo.py.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

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


# --- trade() grammar gate ------------------------------------------------------------------
# `bevo.trade(command=…)` is the one money rail, so the command string is the only place
# the grammar lives. These mirror bevo-docker's duty shim refusals
# (api/scripts/bevo-duty-shim.py): a shape the real rails reject must fail in the replay,
# not at the venue. One test per rule.


def _refusal(**kwargs) -> str:
    with pytest.raises(stub_bevo.BevoError) as exc:
        stub_bevo.trade(idempotency_key="k", **kwargs)
    return str(exc.value)


def test_trade_refuses_a_command_that_is_not_acp_trade():
    assert "must start with `acp trade`" in _refusal(command="acp wallet send-transaction --to 0x1 --data 0x")


def test_trade_refuses_an_empty_or_non_string_command():
    assert "non-empty" in _refusal(command="   ")
    assert "non-empty" in _refusal(command=42)


def test_trade_refuses_free_text_message():
    assert "free text" in _refusal(command="acp trade --token-in usdc --amount-in 5 --token-out 0xabc", message="buy me some")


def test_trade_refuses_amount_in_together_with_amount_usdc():
    msg = _refusal(command="acp trade --token-in usdc --amount-in 5 --amount-usdc 5 --token-out 0xabc")
    assert "different grammars" in msg


def test_trade_refuses_a_side_order_that_also_carries_token_in():
    msg = _refusal(command="acp trade --side long --token-in usdc --amount-usdc 50 --leverage 2")
    assert "--token <SYM>" in msg


def test_trade_refuses_a_stock_sell_with_no_chain():
    msg = _refusal(command="acp trade --token AAPL --amount-shares 2")
    assert "must name its venue" in msg


def test_trade_refuses_a_stock_sell_whose_chain_is_a_chain_id():
    # A numeric --chain is rerouted onto a bare-symbol spot swap — a DIFFERENT asset.
    msg = _refusal(command="acp trade --token AAPL --amount-shares 2 --chain 1")
    assert "VENUE NAME" in msg


def test_trade_does_not_accept_chain_in_as_the_stock_venue():
    """`--chain-in` is the swap rail's flag: it must not satisfy the stock rail's
    `--chain`, or a sell goes out with no venue at all."""
    msg = _refusal(command="acp trade --token AAPL --amount-shares 2 --chain-in 8453")
    assert "must name its venue" in msg


def test_trade_refuses_a_zero_or_negative_amount():
    assert "0 or less" in _refusal(command="acp trade --token-in usdc --amount-in 0 --token-out 0xabc")
    assert "0 or less" in _refusal(command="acp trade --side long --token BTC --amount-usdc -10 --leverage 2")
    assert "0 or less" in _refusal(command="acp trade --token AAPL --amount-shares 0 --chain eth")
    assert "0 or less" in _refusal(command="acp trade --side short --token BTC --size 0 --reduce-only")


def test_trade_refuses_an_amount_that_is_not_a_number():
    assert "needs a number" in _refusal(command="acp trade --token-in usdc --amount-in --token-out 0xabc")


@pytest.mark.parametrize(
    "command",
    [
        "acp trade --token-in usdc --amount-in 25 --token-out 0xabc --chain-out 8453",
        "acp trade --token-in 0xabc --chain-in 8453 --amount-in 20 --token-out usdc",
        "acp trade --side long --token BTC --amount-usdc 500 --leverage 5",
        "acp trade --side short --token BTC --size 0.0125 --reduce-only",
        "acp trade --token AAPL --amount-usdc 200",
        "acp trade --token AAPL --amount-shares 5 --chain eth",
    ],
)
def test_trade_accepts_and_records_every_documented_shape(command):
    stub_bevo.RECORDED_ACTIONS.clear()
    result = stub_bevo.trade(command=command, idempotency_key="copytrade:svc:1")
    assert result["status"] == "accepted"
    assert stub_bevo.RECORDED_ACTIONS == [
        {"call": "trade", "command": command, "params": None, "message": None, "key": "copytrade:svc:1"}
    ]
    stub_bevo.RECORDED_ACTIONS.clear()


# --- read()/holdings()/state ---------------------------------------------------------------


def test_read_answers_user_assets_with_or_without_the_fresh_param():
    """`?fresh=1` is load-bearing on the live rails (without it the server's
    stale-while-revalidate cache serves a PRE-trade balance for up to ten
    minutes) and must not send the stub looking for a different fixture."""
    plain = stub_bevo.read("/user-assets")
    fresh = stub_bevo.read("/user-assets?fresh=1")
    assert plain == fresh
    assert plain["spot"]["available"] is True
    assert plain["perps"]["available"] is True


def test_user_assets_fixture_keeps_stock_shares_apart_from_the_lookalike_token_row():
    """The invariant a stock sell has to respect: `spot.stocks[]` is what the
    venue sells, `spot.tokens[]` is the raw on-chain balance, and on a
    share-multiplier venue they disagree. Sizing off the token row sells 100x."""
    assets = stub_bevo.read("/user-assets")
    stock = next(s for s in assets["spot"]["stocks"] if s["ticker"] == "AAPL")
    token_row = next(t for t in assets["spot"]["tokens"] if t["symbol"] == "AAPL")
    assert stock["shares"] != token_row["balance"]
    assert stock["shares"] == 12.5 and token_row["balance"] == 1250.0
    assert not str(stock["chain"]).isdigit(), "spot.stocks[].chain is the venue name, never a chain id"


def test_user_assets_fixture_carries_a_hip3_namespaced_perp_beside_a_plain_one():
    positions = stub_bevo.read("/user-assets")["perps"]["positions"]
    coins = [p["coin"] for p in positions]
    assert "xyz:AAPL" in coins, "a close must match the FULL HIP-3 coin id"
    assert "BTC" in coins
    for row in positions:
        assert row["side"] in ("long", "short")
        assert row["size"] > 0


def test_read_answers_token_search_from_its_own_fixture():
    body = stub_bevo.read("/token-search", {"q": "$VIRTUAL"})
    hits = {t["address"].lower(): t for t in body["tokens"]}
    assert hits["0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"]["priceUsd"] == 1.3


def test_holdings_reads_the_same_user_assets_fixture():
    rows = stub_bevo.holdings()
    assert [h.symbol for h in rows] == ["VIRTUAL", "VIRTUAL", "AIXBT", "USDC", "AAPL"]
    base_virtual = next(h for h in rows if h.symbol == "VIRTUAL" and h.chain_id == 8453)
    assert base_virtual.amount == 180.5
    assert base_virtual.price_usd == 1.3
    assert base_virtual.address == "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"
    # The same token on two chains is two rows, never one summed total: a sell
    # settles on ONE chain.
    assert sorted(h.chain_id for h in rows if h.symbol == "VIRTUAL") == [1, 8453]


def test_holdings_is_empty_when_there_is_no_user_assets_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(stub_bevo, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(stub_bevo, "FIXTURES_URL", "")
    assert stub_bevo.holdings() == []


def test_state_persists_to_the_replay_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BEVO_STATE_PATH", str(tmp_path / "state.json"))
    first = stub_bevo._State()
    assert first.get("last_id") is None
    first["last_id"] = 42
    assert json.loads((tmp_path / "state.json").read_text()) == {"last_id": 42}
    # A later run in the same state dir reads it back — what a duty's "already
    # told / last seen" bookkeeping relies on.
    assert stub_bevo._State().get("last_id") == 42


def test_state_resolves_its_path_lazily_from_the_working_directory(tmp_path, monkeypatch):
    """replay.py chdir's into --state-dir AFTER importing the stub, so the path
    cannot be bound at import time."""
    monkeypatch.delenv("BEVO_STATE_PATH", raising=False)
    fresh = stub_bevo._State()
    monkeypatch.chdir(tmp_path)
    fresh["seen"] = ["a"]
    assert json.loads((tmp_path / "state.json").read_text()) == {"seen": ["a"]}
