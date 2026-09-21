"""test_stub_rails.py — unit tests for tests/stub_bevo.py: the typed waiters
over fixtures/trade-activity-mixed.jsonl, `read()`'s fixture-name derivation
(including the `?fresh=1` invariant), `holdings()`/`stocks()`/`positions()`
over fixtures/user-assets.json, `state`/`allow()`, `prompt()`/`decide()`
always raising, and — the core of the 2026-09-21 rewrite — the subprocess
monkeypatch that intercepts a shelled `acp trade`/`wallet`/`card` command
instead of spawning the real CLI.

Loaded by file path (like tests/conftest.py loads scripts/build_index.py) so
this needs no package/sys.path setup and matches how replay.py itself loads
stub_bevo.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
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


# --- the subprocess money interception -------------------------------------------------


def test_acp_trade_is_recorded_and_never_actually_spawned(monkeypatch):
    stub_bevo.RECORDED_ACTIONS.clear()
    result = subprocess.run(
        ["acp", "trade", "--token-in", "usdc", "--amount-in", "5", "--token-out", "VIRTUAL",
         "--idempotency-key", "buy:svc:1"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["status"] == "accepted"
    assert body["idempotencyKey"] == "buy:svc:1"
    assert stub_bevo.RECORDED_ACTIONS == [{
        "call": "acp",
        "argv": ["acp", "trade", "--token-in", "usdc", "--amount-in", "5", "--token-out", "VIRTUAL",
                 "--idempotency-key", "buy:svc:1"],
        "key": "buy:svc:1",
    }]
    stub_bevo.RECORDED_ACTIONS.clear()


@pytest.mark.parametrize("subcommand", ["trade", "wallet", "card"])
def test_every_money_subcommand_is_intercepted(subcommand):
    stub_bevo.RECORDED_ACTIONS.clear()
    subprocess.run(["acp", subcommand, "--idempotency-key", "k"], capture_output=True, text=True, check=False)
    assert len(stub_bevo.RECORDED_ACTIONS) == 1
    stub_bevo.RECORDED_ACTIONS.clear()


def test_a_non_money_acp_command_is_not_intercepted():
    """`acp --help` / a read subcommand is not a spend and must reach the
    real CLI — which is not installed in the test sandbox, so this raises
    FileNotFoundError rather than being silently swallowed."""
    with pytest.raises(FileNotFoundError):
        subprocess.run(["acp", "--help"], capture_output=True, text=True, check=False)


def test_a_command_that_is_not_acp_is_untouched():
    result = subprocess.run(["echo", "hello"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_missing_key_is_recorded_as_none():
    stub_bevo.RECORDED_ACTIONS.clear()
    subprocess.run(["acp", "trade", "--token-in", "usdc"], capture_output=True, text=True, check=False)
    assert stub_bevo.RECORDED_ACTIONS[-1]["key"] is None
    stub_bevo.RECORDED_ACTIONS.clear()


def test_exec_status_finds_a_recorded_key():
    stub_bevo.RECORDED_ACTIONS.clear()
    subprocess.run(["acp", "trade", "--idempotency-key", "abc"], capture_output=True, text=True, check=False)
    assert stub_bevo.exec_status("abc")["state"] == "executed"
    assert stub_bevo.exec_status("no-such-key")["state"] == "unknown"
    stub_bevo.RECORDED_ACTIONS.clear()


# --- prompt() / decide() ------------------------------------------------------------------


def test_prompt_always_raises_rehearsal_style():
    with pytest.raises(stub_bevo.BevoError) as exc:
        stub_bevo.prompt("bullish or bearish?")
    assert exc.value.code == "rehearsal"


def test_decide_raises_through_prompt():
    with pytest.raises(stub_bevo.BevoError):
        stub_bevo.decide("pick one", ["a", "b"])


def test_decide_validates_option_count():
    with pytest.raises(ValueError):
        stub_bevo.decide("pick one", ["only-one"])


# --- escalate() shim -----------------------------------------------------------------------


def test_escalate_is_a_refusing_shim_not_a_crash():
    result = stub_bevo.escalate("why", [])
    assert result == {"accepted": False, "error": "escalate is retired"}


# --- read()/holdings()/state ---------------------------------------------------------------


def test_read_answers_user_assets_with_or_without_the_fresh_param():
    plain = stub_bevo.read("/user-assets")
    fresh = stub_bevo.read("/user-assets?fresh=1")
    assert plain == fresh
    assert plain["spot"]["available"] is True


def test_holdings_reads_the_same_user_assets_fixture():
    rows = stub_bevo.holdings()
    assert isinstance(rows, list)
    # `Holding` objects, as the container returns — not the raw wire dict.
    assert all(isinstance(r, stub_bevo.Holding) for r in rows)
    assert any(r.symbol == "VIRTUAL" for r in rows)


def test_holdings_is_empty_when_there_is_no_user_assets_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(stub_bevo, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(stub_bevo, "FIXTURES_URL", "")
    assert stub_bevo.holdings() == []


def test_read_answers_token_search_from_its_own_fixture():
    body = stub_bevo.read("/token-search", {"q": "$VIRTUAL"})
    hits = {t["address"].lower(): t for t in body["tokens"]}
    assert hits["0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"]["priceUsd"] == 1.3


def test_state_persists_to_the_replay_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BEVO_STATE_PATH", str(tmp_path / "state.json"))
    first = stub_bevo._State()
    assert first.get("last_id") is None
    first["last_id"] = 42
    assert json.loads((tmp_path / "state.json").read_text()) == {"last_id": 42}
    assert stub_bevo._State().get("last_id") == 42


def test_state_resolves_its_path_lazily_from_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("BEVO_STATE_PATH", raising=False)
    fresh = stub_bevo._State()
    monkeypatch.chdir(tmp_path)
    fresh["seen"] = ["a"]
    assert json.loads((tmp_path / "state.json").read_text()) == {"seen": ["a"]}


def test_allow_consumes_before_the_spend_and_gates_on_every_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("BEVO_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(stub_bevo, "state", stub_bevo._State())
    assert stub_bevo.allow("k", per_day=2) is True
    assert stub_bevo.allow("k", per_day=2) is True
    assert stub_bevo.allow("k", per_day=2) is False  # third call this day is over budget


# --- the waiters -----------------------------------------------------------------------


def test_trades_replays_the_fixture_jsonl(monkeypatch):
    monkeypatch.setenv("BEVO_STUB_FIXTURE", "trade-activity-page")
    monkeypatch.setattr(stub_bevo, "FIXTURE_NAME", "trade-activity-page")
    rows = list(stub_bevo.trades())
    assert len(rows) >= 1
    # Typed objects, not the raw wire dict — the container's waiters yield
    # TradeEvent, and a stub that yielded dicts made `isinstance(ev,
    # bevo.TradeEvent)` false for every row.
    assert all(isinstance(r, stub_bevo.TradeEvent) for r in rows)
    assert all(r.direction is not None for r in rows)


def test_batches_yields_one_batch_per_event(monkeypatch):
    monkeypatch.setattr(stub_bevo, "FIXTURE_NAME", "trade-activity-page")
    batches = list(stub_bevo.batches())
    assert all(len(b) == 1 for b in batches)
    assert len(batches) == len(list(stub_bevo.events()))


def test_typed_returns_the_class_a_duty_isinstance_checks(monkeypatch):
    """`bevo.typed(raw)` must return a TradeEvent, not the raw envelope.

    butler-skill-copytrade's whole loop is

        for batch in bevo.batches(seconds=3):
            for raw in batch:
                ev = bevo.typed(raw)
                if not isinstance(ev, bevo.TradeEvent) or ev.id is None:
                    continue

    `typed()` used to return its argument untouched and the module exported no
    `TradeEvent` at all, so the replay died on `AttributeError: module 'bevo'
    has no attribute 'TradeEvent'`. Exporting the name alone would have been
    worse: every row would then have failed the isinstance check silently, the
    duty would have skipped all of them, and the replay would have reported a
    pass with zero actions recorded.
    """
    monkeypatch.setattr(stub_bevo, "FIXTURE_NAME", "trade-activity-page")
    raws = [b[0] for b in stub_bevo.batches()]
    assert raws, "fixture yielded nothing"

    typed = [stub_bevo.typed(raw) for raw in raws]
    trades = [t for t in typed if isinstance(t, stub_bevo.TradeEvent)]
    assert trades, "no row survived the isinstance check a real template makes"
    assert all(t.id is not None for t in trades)


def test_every_event_type_the_container_exports_is_exported_here():
    """A duty may name any of these directly, so a missing one is an
    AttributeError at replay rather than a diff anyone reads."""
    for name in (
        "Asset", "Balance", "GroupMessage", "Holding", "HttpPollEvent",
        "PerpPosition", "StockHolding", "TimerTick", "TradeEvent",
        "WalletTransfer", "WebhookEvent", "WebsocketFrame",
    ):
        assert hasattr(stub_bevo, name), f"stub_bevo does not export {name}"
