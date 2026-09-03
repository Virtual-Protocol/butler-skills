"""stub_bevo.py — the offline `bevo` module.

A port of bevo-docker's `api/bevo_services/sdk_rehearsal.py` recording
semantics: `events()` replays a fixture JSONL file, `trade()`/`execute()`/
`notify()` record their call (including the idempotency key) into a list
instead of acting, `read()`/`rpc()` answer from fixture JSON files, and
`log()` just prints. `tests/replay.py` puts this module on `sys.path` as
`bevo` so a skill's real, unmodified `duty.py` can `import bevo` and run
against captured data with no network and no container.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

FIXTURES_DIR = Path(os.environ.get("BEVO_STUB_FIXTURES_DIR", str(Path(__file__).parent / "fixtures")))
FIXTURE_NAME = os.environ.get("BEVO_STUB_FIXTURE", "trade-activity-page")

SERVICE_ID = os.environ.get("BEVO_STUB_SERVICE_ID", "stub-service-id")
SESSION_ID = os.environ.get("BEVO_STUB_SESSION_ID", "stub-session-id")

RECORDED_ACTIONS: list[dict] = []


class BevoError(Exception):
    pass


def _fixture_path(name: str) -> Path:
    return FIXTURES_DIR / name


def events():
    """Replay the fixture JSONL as a generator of event envelopes."""
    path = _fixture_path(f"{FIXTURE_NAME}.jsonl")
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def log(msg: str) -> None:
    print(f"[stub_bevo.log] {msg}")


def notify(text: str) -> dict:
    action = {"call": "notify", "text": text, "key": None}
    RECORDED_ACTIONS.append(action)
    return {"status": "accepted"}


def trade(command=None, params=None, message=None, idempotency_key=None, max_attempts=3) -> dict:
    action = {
        "call": "trade",
        "command": command,
        "params": params,
        "message": message,
        "key": idempotency_key,
    }
    RECORDED_ACTIONS.append(action)
    return {"status": "accepted", "idempotencyKey": idempotency_key, "executionLogId": len(RECORDED_ACTIONS)}


def execute(to, data="0x", value=None, chain_id=8453, idempotency_key=None, max_attempts=3) -> dict:
    action = {
        "call": "execute",
        "to": to,
        "data": data,
        "value": value,
        "chainId": chain_id,
        "key": idempotency_key,
    }
    RECORDED_ACTIONS.append(action)
    return {"status": "accepted", "idempotencyKey": idempotency_key, "approvalId": len(RECORDED_ACTIONS)}


def read(path: str, params: dict | None = None):
    """Answer a bevo.read(...) call from tests/fixtures/<slug>.json.

    The fixture file name is derived from the last non-empty path segment,
    e.g. "/me" -> me.json, "/user-assets" -> user-assets.json.
    """
    slug = path.strip("/").split("/")[-1] or "index"
    fp = _fixture_path(f"{slug}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for read({path!r}) — expected {fp}")
    return json.loads(fp.read_text())


def rpc(chain_id, method, params=None):
    """Answer a bevo.rpc(...) call from tests/fixtures/rpc-<method>.json."""
    fp = _fixture_path(f"rpc-{method}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for rpc(chain_id={chain_id}, method={method!r}) — expected {fp}")
    return json.loads(fp.read_text())


def exec_status(key: str, route: str = "trade"):
    for action in RECORDED_ACTIONS:
        if action.get("key") == key:
            return {"state": "executed", "route": route, "idempotencyKey": key}
    return {"state": "unknown", "route": route, "idempotencyKey": key}


def escalate(reason: str, events_list) -> dict:
    action = {"call": "escalate", "reason": reason, "events": events_list, "key": None}
    RECORDED_ACTIONS.append(action)
    return {"status": "escalated"}
