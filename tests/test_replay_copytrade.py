"""test_replay_copytrade.py — replay butler-copytrade's duty.py against the
captured trade-activity-page fixture and assert: exactly one recorded
buy per leader buy on an allowed chain, with distinct keys, and none for
the sell / null-field / off-chain rows; a second run against the same
state directory records zero new trades (the stub's idempotency ledger,
standing in for the real container's per-key dedup, does the work the
skill's own now-removed seen-set used to do).

The skill is not checked out here — it is cloned at the ref skills.json
follows by the session-scoped `copytrade_checkout` fixture (tests/conftest.py),
which skips these tests when the clone is unavailable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY = REPO_ROOT / "tests" / "replay.py"

# SIZING=leader_share SHARE=1 copies 1:1 off the leader's own usd_value, so a
# null usd_value (event 6) sizes to zero and is skipped the same way a named
# size of zero always is; CHAIN_IDS=[8453] scopes to the fixture's own chain,
# so the chain-1 event (5) is never even considered.
ENV = [
    "LEADER=11111111-1111-1111-1111-111111111111",
    "SIZING=leader_share",
    "SHARE=1",
    "CHAIN_IDS=[8453]",
]


def run_replay(skill: Path, state_dir: Path):
    # A clone in a temp directory is not `skills/<name>` in a registry checkout,
    # so replay runs it the way a skill author does: --standalone.
    cmd = [sys.executable, str(REPLAY), "--standalone", str(skill), "--fixture", "trade-activity-page", "--state-dir", str(state_dir)]
    for kv in ENV:
        cmd += ["--env", kv]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    json_text = proc.stdout.split("# ACTIONS_JSON_START\n")[1].split("# ACTIONS_JSON_END")[0]
    actions = json.loads(json_text)
    return actions


def test_one_trade_per_buy_distinct_keys(tmp_path, copytrade_checkout):
    state_dir = tmp_path / "state1"
    state_dir.mkdir()
    actions = run_replay(copytrade_checkout, state_dir)
    trades = [a for a in actions if a["call"] == "buy"]
    assert len(trades) == 3, trades  # events 1, 2, 4 are the only valid buys on chain 8453
    keys = [t["key"] for t in trades]
    assert len(keys) == len(set(keys)), "expected distinct idempotency keys"
    for t in trades:
        assert t["key"]
        assert t["key"].startswith("copytrade:")


def test_no_trade_for_sell_or_null_or_offchain(tmp_path, copytrade_checkout):
    state_dir = tmp_path / "state2"
    state_dir.mkdir()
    actions = run_replay(copytrade_checkout, state_dir)
    trades = [a for a in actions if a["call"] == "buy"]
    traded_commands = " ".join(t["command"] for t in trades)
    # event 3 (sell) trades USDC out, not in; event 5's chain 1 amount is 100; event 6 has null usdValue.
    assert "--amount-in 100" not in traded_commands  # chain-1 event never copied
    assert "--amount-in 20" not in traded_commands  # null usdValue event never copied


def test_replaying_same_page_twice_records_no_new_trades(tmp_path, copytrade_checkout):
    state_dir = tmp_path / "state3"
    state_dir.mkdir()
    first = run_replay(copytrade_checkout, state_dir)
    first_trades = [a for a in first if a["call"] == "buy"]
    assert len(first_trades) == 3

    second = run_replay(copytrade_checkout, state_dir)
    second_trades = [a for a in second if a["call"] == "buy"]
    assert second_trades == [], f"expected zero new trades on replay, got {second_trades}"


def test_standalone_replay_from_any_directory(tmp_path, copytrade_checkout):
    """`tests/replay.py --standalone <dir>` is what the template's CI runs from an
    author's own repo: copy the skill somewhere unrelated (no .git, arbitrary dir
    name), run from an unrelated cwd, and expect the same three recorded trades."""
    checkout = tmp_path / "butler-skill-copytrade-checkout"
    shutil.copytree(copytrade_checkout, checkout, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    cmd = [sys.executable, str(REPLAY), "--standalone", str(checkout), "--fixture", "trade-activity-page", "--state-dir", str(state_dir)]
    for kv in ENV:
        cmd += ["--env", kv]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "# standalone replay of butler-copytrade from" in proc.stdout
    actions = json.loads(proc.stdout.split("# ACTIONS_JSON_START\n")[1].split("# ACTIONS_JSON_END")[0])
    assert len([a for a in actions if a["call"] == "buy"]) == 3


