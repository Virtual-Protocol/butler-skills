"""test_replay_copytrade.py — replay butler-copytrade's duty.py offline and
assert on the `acp trade` COMMAND STRINGS it files.

`bevo.trade(command=…)` is the one money rail: the seven money verbs
(`buy`/`sell`/`long`/`short`/`close`/`stock_buy`/`stock_sell`) are gone, so the
command string is now the only place the grammar lives. Counting recorded
actions would no longer prove anything about it — a duty that reversed
`--token-in`/`--token-out`, dropped `--chain-out`, or sized a stock sell off the
wrong array records exactly the same number of actions. So these read the
strings.

Two fixtures, because they cover different halves of the grammar:
`trade-activity-page` is BUYS ONLY (the swap shape), `trade-activity-mixed`
adds a sell, a perp open, a perp close, a liquidation and a tokenized-stock
buy and sell — the legs that have to derive a quantity from
`bevo.read("/user-assets")` (fixtures/user-assets.json).

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

# The three buys of trade-activity-page, in feed order. A buy spends usdc as
# --token-in; the token bought is --token-out with the leader's OWN chain on
# --chain-out; --amount-in is the USD the copy spends (leader_share at 1.0, so
# the leader's own usd_value verbatim). Event 4's tokenOutSymbol is null and it
# still copies: the address is what trades, the symbol is a display name.
PAGE_COMMANDS = [
    "acp trade --token-in usdc --amount-in 25 --token-out 0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b --chain-out 8453",
    "acp trade --token-in usdc --amount-in 40 --token-out 0x4f9fd6be4a90f2620860d680c0d4d5fb53d1a825 --chain-out 8453",
    "acp trade --token-in usdc --amount-in 15 --token-out 0x9a8c3f3c9d4e8f1a2b3c4d5e6f7081920a1b2c3d --chain-out 8453",
]
PAGE_KEYS = [
    "copytrade:stub-service-id:1",
    "copytrade:stub-service-id:2",
    "copytrade:stub-service-id:4",
]


def run_replay(skill: Path, state_dir: Path, fixture: str = "trade-activity-page", env: list[str] | None = None):
    # A clone in a temp directory is not `skills/<name>` in a registry checkout,
    # so replay runs it the way a skill author does: --standalone.
    cmd = [sys.executable, str(REPLAY), "--standalone", str(skill), "--fixture", fixture, "--state-dir", str(state_dir)]
    for kv in env if env is not None else ENV:
        cmd += ["--env", kv]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    json_text = proc.stdout.split("# ACTIONS_JSON_START\n")[1].split("# ACTIONS_JSON_END")[0]
    actions = json.loads(json_text)
    return actions


def trades_of(actions: list[dict]) -> list[dict]:
    """The recorded money actions. `trade` is the only call a copy files —
    there is no per-verb call to filter on any more."""
    return [a for a in actions if a["call"] == "trade"]


def test_one_trade_per_buy_with_the_swap_grammar_and_distinct_keys(tmp_path, copytrade_checkout):
    state_dir = tmp_path / "state1"
    state_dir.mkdir()
    trades = trades_of(run_replay(copytrade_checkout, state_dir))

    assert [t["command"] for t in trades] == PAGE_COMMANDS
    assert [t["key"] for t in trades] == PAGE_KEYS
    assert len(set(PAGE_KEYS)) == len(PAGE_KEYS), "expected distinct idempotency keys"
    for t in trades:
        # command= is the rail; params= is spot-swap only and silently strips
        # every perp/stock field, so a skill must never reach for it.
        assert t["params"] is None
        assert t["message"] is None, "a trade is a command, never free text"


def test_no_trade_for_sell_or_null_or_offchain(tmp_path, copytrade_checkout):
    state_dir = tmp_path / "state2"
    state_dir.mkdir()
    trades = trades_of(run_replay(copytrade_checkout, state_dir))
    traded_commands = " ".join(t["command"] for t in trades)

    # event 3 (sell) trades USDC out, not in; event 5's chain 1 amount is 100; event 6 has null usdValue.
    assert "--amount-in 100" not in traded_commands  # chain-1 event never copied
    assert "--amount-in 20" not in traded_commands  # null usdValue event never copied
    assert " --chain-out 1" not in traded_commands  # nothing lands off the allowed chain
    assert "--token-out usdc" not in traded_commands  # MIRROR_SELLS is off: no sell leg at all


def test_replaying_the_same_page_re_emits_the_same_idempotency_keys(tmp_path, copytrade_checkout):
    """The stub has no per-key ledger and replay.py's duplicate check is built
    fresh per process, so a second run records the same actions again — that is
    the point. What must hold is that the KEY is derived from the leader event
    and nothing else, so the two runs emit identical keys: the real container's
    server-side per-key dedup at POST /butler-exec/trade then answers "already
    filed" instead of trading twice. A key that varied per run (a timestamp, a
    uuid) would defeat that dedup silently."""
    state_dir = tmp_path / "state3"
    state_dir.mkdir()
    first = trades_of(run_replay(copytrade_checkout, state_dir))
    second = trades_of(run_replay(copytrade_checkout, state_dir))

    assert [t["key"] for t in first] == PAGE_KEYS
    assert [t["key"] for t in second] == [t["key"] for t in first]
    assert [t["command"] for t in second] == [t["command"] for t in first]


def test_mixed_fixture_sizes_every_leg_off_the_user_assets_snapshot(tmp_path, copytrade_checkout):
    """The legs trade-activity-page can never reach. Each quantity below is
    derived from fixtures/user-assets.json, and each one is a trap the removed
    money verbs used to hide:

      * the VIRTUAL sell takes the BASE row (180.5 @ $1.30), not the bigger
        chain-1 row, and carries that row's own --chain-in;
      * the BTC close sends the opposite of the side WE are on, sized by the
        position's own `size`, with --reduce-only;
      * the ETH liquidation closes a SHORT (ours) even though the leader's row
        was a long;
      * the AAPL stock sell is `--token`/`--amount-shares` with NO --side, its
        5 shares sized off spot.stocks[] (12.5 shares @ $190), never the
        look-alike spot.tokens[] row (1250 raw tokens), and --chain is the
        VENUE NAME.
    """
    state_dir = tmp_path / "state4"
    state_dir.mkdir()
    env = ENV + ["MIRROR_SELLS=true", "MIRROR_PERPS=true", "MIRROR_STOCKS=true"]
    trades = trades_of(run_replay(copytrade_checkout, state_dir, fixture="trade-activity-mixed", env=env))

    assert [t["command"] for t in trades] == [
        "acp trade --token-in usdc --amount-in 25 --token-out 0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b --chain-out 8453",
        "acp trade --token-in 0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b --chain-in 8453 --amount-in 20 --token-out usdc",
        "acp trade --side long --token BTC --amount-usdc 500 --leverage 5",
        "acp trade --side short --token BTC --size 0.0125 --reduce-only",
        "acp trade --side long --token ETH --size 0.42 --reduce-only",
        "acp trade --token AAPL --amount-usdc 200",
        "acp trade --token AAPL --amount-shares 5 --chain eth",
    ]
    assert [t["key"] for t in trades] == [f"copytrade:stub-service-id:{i}" for i in range(101, 108)]

    stock_sell = trades[-1]["command"]
    assert " --side " not in stock_sell, "a stock order takes no --side"
    assert "--amount-shares 1250" not in stock_sell, "sized off spot.tokens[], not spot.stocks[]"
    assert not stock_sell.split("--chain ")[1].split()[0].isdigit(), "--chain must be the venue name"


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
    assert [a["command"] for a in trades_of(actions)] == PAGE_COMMANDS
