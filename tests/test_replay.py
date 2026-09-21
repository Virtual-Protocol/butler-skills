"""test_replay.py — pytest coverage for tests/replay.py against the local
`valid` fixture template (tests/fixtures/templates/valid), which buys VIRTUAL
off every recorded buy leg in fixtures/trade-activity-page.jsonl, one shelled
`acp trade ... --idempotency-key ...` per buy — recorded, not spawned, by
stub_bevo.py's subprocess monkeypatch.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID = REPO_ROOT / "tests" / "fixtures" / "templates" / "valid"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd))


def _actions(stdout: str) -> list[dict]:
    return json.loads(stdout.split("# ACTIONS_JSON_START\n")[1].split("# ACTIONS_JSON_END")[0])


def test_replay_records_one_acp_trade_per_buy_leg(tmp_path):
    proc = _run(
        [sys.executable, str(REPO_ROOT / "tests" / "replay.py"), "--standalone", str(VALID),
         "--fixture", "trade-activity-page", "--no-download"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    actions = _actions(proc.stdout)
    assert actions, "expected at least one recorded acp trade"
    assert all(a["call"] == "acp" for a in actions)
    keys = [a["key"] for a in actions]
    assert all(keys)  # every action carries a key
    assert len(keys) == len(set(keys))  # and no two share one
    assert "# standalone replay of valid from" in proc.stdout


def test_replay_fails_on_a_duplicate_idempotency_key(tmp_path):
    """Two events resolving to the same key must fail the replay — the whole
    point of checking is catching a duty whose key derivation collides."""
    template = tmp_path / "dup-key"
    shutil.copytree(VALID, template)
    (template / "duty.py").write_text(
        "import subprocess\nimport bevo\n\n"
        "def main():\n"
        "    for trade in bevo.trades():\n"
        "        subprocess.run(['acp', 'trade', '--idempotency-key', 'always-the-same'],"
        " capture_output=True, text=True, check=False)\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    proc = _run(
        [sys.executable, str(REPO_ROOT / "tests" / "replay.py"), "--standalone", str(template),
         "--fixture", "trade-activity-page", "--no-download"],
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    assert "duplicate idempotency key" in proc.stdout


def test_replay_fails_loudly_when_a_key_is_missing(tmp_path):
    template = tmp_path / "no-key"
    shutil.copytree(VALID, template)
    (template / "duty.py").write_text(
        "import subprocess\nimport bevo\n\n"
        "def main():\n"
        "    for trade in bevo.trades():\n"
        "        subprocess.run(['acp', 'trade'], capture_output=True, text=True, check=False)\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    proc = _run(
        [sys.executable, str(REPO_ROOT / "tests" / "replay.py"), "--standalone", str(template),
         "--fixture", "trade-activity-page", "--no-download"],
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    assert "missing an idempotency key" in proc.stdout


def test_replay_is_skipped_cleanly_for_a_template_with_no_duty_py(tmp_path):
    template = tmp_path / "one-off"
    shutil.copytree(VALID, template)
    (template / "duty.py").unlink()
    proc = _run(
        [sys.executable, str(REPO_ROOT / "tests" / "replay.py"), "--standalone", str(template),
         "--fixture", "trade-activity-page", "--no-download"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing to replay" in proc.stdout
