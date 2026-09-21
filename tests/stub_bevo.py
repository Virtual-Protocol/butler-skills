"""stub_bevo.py — the offline `bevo` module.

A replay stand-in for `virtuals-agent`'s `src/integrations/butler/bin/py/bevo.py`:
the typed waiters (`events`/`trades`/`messages`/`transfers`/`ticks`/`polls`/
`webhooks`/`frames`/`batches`/`typed`) replay a fixture JSONL file, `read()`/
`rpc()` answer from fixture JSON files, `balance()`/`holdings()`/`stocks()`/
`positions()` are read()'s own answer reshaped, `state` is a dict on disk in
the replay's state directory, `allow()` is the same UTC-bucket rate limiter,
`log()`/`notify()` record instead of reaching anyone, and `prompt()`/
`decide()` always raise `BevoError(code="rehearsal")` — exactly what the real
SDK does in `BEVO_MODE=rehearsal`, because a replay must never call a real
model. `replay.py` puts this module on `sys.path` as `bevo` so a template's
real, unmodified `duty.py` can `import bevo` and run against captured data
with no network and no container.

**There is no money verb.** Since 2026-09-21 a duty spends by shelling
`acp trade`/`acp wallet send-transaction`/`acp card issue` directly — see
`bevo_ast.py`'s `RETIRED` set in `virtuals-agent`. This stub therefore does
not offer `trade()`/`execute()`/`buy()`/... at all; a duty.py that still
calls one fails here with an `AttributeError`, the same way it would in a
real container. What this stub DOES do is monkeypatch `subprocess.run` (and
`.check_output`/`.Popen`/`.call`/`.check_call`) at import time: an argv whose
first two elements are literally `"acp"` and a money subcommand
(`trade`/`wallet`/`card`) is intercepted and RECORDED instead of actually
spawning the real CLI, and a synthetic `{"status": "accepted", ...}` JSON
reply is returned on stdout — everything else passes through to the real
`subprocess` so a duty's other shell-outs (there should not be any; the
validator refuses most of them) behave normally.

Fixtures live in `fixtures/` next to this file (BEVO_STUB_FIXTURES_DIR
overrides). When BEVO_STUB_FIXTURES_URL is set (replay.py sets it to the hub's
published fixtures directory) a fixture that is missing locally is downloaded
from `<url>/<name>` on first use — the only network this stub ever touches,
and only for files that are not already on disk.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess as _real_subprocess
import time
from pathlib import Path

FIXTURES_DIR = Path(os.environ.get("BEVO_STUB_FIXTURES_DIR", str(Path(__file__).parent / "fixtures")))
FIXTURE_NAME = os.environ.get("BEVO_STUB_FIXTURE", "trade-activity-page")
FIXTURES_URL = os.environ.get("BEVO_STUB_FIXTURES_URL", "").rstrip("/")

SERVICE_ID = os.environ.get("BEVO_SERVICE_ID", "stub-service-id")
SESSION_ID = os.environ.get("BEVO_SESSION_ID", "stub-session-id")

RECORDED_ACTIONS: list[dict] = []

MONEY_BIN = "acp"
MONEY_SUBCOMMANDS = ("trade", "wallet", "card")
IDEMPOTENCY_FLAG = "--idempotency-key"


class BevoError(Exception):
    def __init__(self, message, code=None, retry_after_s=None):
        super().__init__(message)
        self.code = code
        self.retry_after_s = retry_after_s


def _download_fixture(name: str, dest: Path) -> bool:
    import urllib.error
    import urllib.request

    url = f"{FIXTURES_URL}/{name}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[stub_bevo] no local fixture {name} and could not download {url}: {e}")
        return False
    print(f"[stub_bevo] downloaded fixture {name} from {url}")
    return True


def _fixture_path(name: str) -> Path:
    fp = FIXTURES_DIR / name
    if not fp.exists() and FIXTURES_URL:
        _download_fixture(name, fp)
    return fp


# --- the event stream ----------------------------------------------------------------------


def _events_raw():
    path = _fixture_path(f"{FIXTURE_NAME}.jsonl")
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def events():
    yield from _events_raw()


_KIND_KEY = {
    "trade": "trades",
    "group": "messages",
    "wallet": "transfers",
    "timer": "ticks",
    "http_poll": "polls",
    "webhook": "webhooks",
    "websocket": "frames",
}


def _typed_kind(kind: str):
    for event in _events_raw():
        if event.get("kind") == kind:
            yield event.get("event") or event.get("message") or event.get("transfer") or event


def trades():
    return _typed_kind("trade")


def messages():
    return _typed_kind("group")


def transfers():
    return _typed_kind("wallet")


def ticks():
    return _typed_kind("timer")


def polls():
    return _typed_kind("http_poll")


def webhooks():
    return _typed_kind("webhook")


def frames():
    return _typed_kind("websocket")


def typed(event):
    return event


def batches(seconds=3.0, max_events=100):
    """No timing to observe when replaying a file all at once: each event is
    its own batch, exactly like the real SDK's rehearsal mode."""
    for event in _events_raw():
        yield [event]


# --- logging and state -----------------------------------------------------------------------


def log(message) -> None:
    print(f"[stub_bevo.log] {message}")


class _State(dict):
    """`bevo.state` — the live SDK's dict-on-disk, saved on every write.

    The file is `state.json` in the process's working directory
    (BEVO_STATE_PATH overrides), which is the replay's `--state-dir`:
    replay.py chdir's there before running duty.py, so the path is resolved
    LAZILY on first touch, not at import time when the cwd is still the
    caller's."""

    def __init__(self):
        super().__init__()
        self._loaded = False

    def _path(self) -> str:
        return os.environ.get("BEVO_STATE_PATH") or os.path.join(os.getcwd(), "state.json")

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            dict.update(self, data)

    def _save(self):
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(dict(self), f)
        except OSError as exc:
            log(f"[stub_bevo] state not saved: {exc}")

    def __getitem__(self, key):
        self._load()
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._load()
        return dict.__contains__(self, key)

    def __iter__(self):
        self._load()
        return dict.__iter__(self)

    def __len__(self):
        self._load()
        return dict.__len__(self)

    def get(self, key, default=None):
        self._load()
        return dict.get(self, key, default)

    def keys(self):
        self._load()
        return dict.keys(self)

    def values(self):
        self._load()
        return dict.values(self)

    def items(self):
        self._load()
        return dict.items(self)

    def __setitem__(self, key, value):
        self._load()
        dict.__setitem__(self, key, value)
        self._save()

    def __delitem__(self, key):
        self._load()
        dict.__delitem__(self, key)
        self._save()

    def setdefault(self, key, default=None):
        self._load()
        if key not in self:
            self[key] = default
            return default
        return dict.__getitem__(self, key)

    def pop(self, key, *args):
        self._load()
        value = dict.pop(self, key, *args)
        self._save()
        return value

    def update(self, *args, **kwargs):
        self._load()
        dict.update(self, *args, **kwargs)
        self._save()


state = _State()


def allow(key, per_day=None, per_hour=None, max_usd=None, usd=0):
    """A port of the real UTC-bucket rate limiter — see bevo.py. Consumes on
    True, before the spend is attempted."""
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    hour = time.strftime("%Y-%m-%dT%H", time.gmtime(now))
    slot = state.get(f"_allow:{key}") or {}

    if slot.get("day") != day:
        slot = {"day": day, "day_n": 0, "day_usd": 0.0, "hour": hour, "hour_n": 0}
    if slot.get("hour") != hour:
        slot["hour"] = hour
        slot["hour_n"] = 0

    spend = float(usd or 0)
    if per_day is not None and slot.get("day_n", 0) >= int(per_day):
        return False
    if per_hour is not None and slot.get("hour_n", 0) >= int(per_hour):
        return False
    if max_usd is not None and float(slot.get("day_usd", 0.0)) + spend > float(max_usd):
        return False

    slot["day_n"] = slot.get("day_n", 0) + 1
    slot["hour_n"] = slot.get("hour_n", 0) + 1
    slot["day_usd"] = float(slot.get("day_usd", 0.0)) + spend
    state[f"_allow:{key}"] = slot
    return True


def sleep(seconds) -> None:
    """A replay never actually sleeps — there is no live clock to wait on."""
    return None


# --- talking to the outside --------------------------------------------------------------


def notify(text, quiet=False) -> dict:
    action = {"call": "notify", "text": str(text)[:500], "quiet": bool(quiet)}
    RECORDED_ACTIONS.append(action)
    return {"ok": True}


def escalate(reason, events=None) -> dict:
    """Retired shim — mirrors the real SDK's: logs and returns, never raises,
    so an old duty on a volume degrades instead of crashing. A NEW duty that
    calls it is refused at filing time (scripts/validate.py)."""
    log("[stub_bevo] escalate is retired — use bevo.prompt()")
    return {"accepted": False, "error": "escalate is retired"}


def prompt(text, *, system=None, schema=None, max_tokens=None):
    """A replay never calls a real model — exactly like BEVO_MODE=rehearsal,
    this always raises, so unguarded code fails here rather than in
    production."""
    raise BevoError(
        "replay never calls the model; live, this raises the same way when "
        "rate-limited or timed out — catch BevoError",
        code="rehearsal",
    )


def decide(question, options, *, context=None):
    options = [str(o) for o in (options or [])]
    if not 2 <= len(options) <= 12:
        raise ValueError("decide() needs between 2 and 12 options")
    return prompt(str(question))


# --- reads ---------------------------------------------------------------------------------


def read(path: str, params: dict | None = None):
    """Answer a bevo.read(...) call from fixtures/<slug>.json.

    The fixture file name is derived from the last non-empty path segment
    with any query string dropped, e.g. "/me" -> me.json,
    "/user-assets" and "/user-assets?fresh=1" both -> user-assets.json.
    `params` narrows nothing: fixtures are per-path, not per-parameter.
    """
    slug = str(path).strip("/").split("/")[-1].split("?")[0] or "index"
    fp = _fixture_path(f"{slug}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for read({path!r}) — expected {fp}")
    return json.loads(fp.read_text())


def rpc(chain_id, method, params=None):
    fp = _fixture_path(f"rpc-{method}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for rpc(chain_id={chain_id}, method={method!r}) — expected {fp}")
    payload = json.loads(fp.read_text())
    return payload.get("result", payload)


def balance():
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError:
        return {"available": False}
    spot = (body or {}).get("spot") or {}
    return {"available": spot.get("available") is True, "raw": body}


def holdings():
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError:
        return []
    tokens = ((body or {}).get("spot") or {}).get("tokens") or []
    return list(tokens)


def stocks():
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError:
        return None
    spot = (body or {}).get("spot") or {}
    if spot.get("available") is not True:
        return None
    return list(spot.get("stocks") or [])


def positions():
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError:
        return None
    perps = (body or {}).get("perps") or {}
    if perps.get("available") is not True:
        return None
    return list(perps.get("positions") or [])


def user(handle):
    try:
        return read("/user", {"username": str(handle).lstrip("@")})
    except BevoError:
        return None


def groups():
    try:
        return (read("/groups") or {}).get("groups") or []
    except BevoError:
        return []


def group_messages(group_id, since=None, limit=100):
    try:
        body = read(f"/groups/{int(group_id)}/messages", {"limit": limit})
    except BevoError:
        return []
    return list((body or {}).get("messages") or [])


def exec_status(key, route="trade"):
    for action in RECORDED_ACTIONS:
        if action.get("key") == key:
            return {"state": "executed", "route": route, "idempotencyKey": key}
    return {"state": "unknown", "route": route, "idempotencyKey": key}


# --- money: intercepting the shelled `acp` command ------------------------------------------


def _extract_key(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == IDEMPOTENCY_FLAG and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _fake_completed_process(argv: list[str]):
    key = _extract_key(argv)
    action = {"call": "acp", "argv": list(argv), "key": key}
    RECORDED_ACTIONS.append(action)
    stdout = json.dumps({"status": "accepted", "idempotencyKey": key})
    return _real_subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")


def _is_acp_money_argv(argv) -> bool:
    return (
        isinstance(argv, (list, tuple))
        and len(argv) >= 2
        and argv[0] == MONEY_BIN
        and argv[1] in MONEY_SUBCOMMANDS
    )


# Captured BEFORE patching — `_real_subprocess` is the actual `subprocess`
# module object (the same one `import subprocess` gets elsewhere), so once
# its attributes are overwritten below, `_real_subprocess.run` would resolve
# to our own patch and recurse forever. These names are the only way back to
# the real implementations.
_ORIG_RUN = _real_subprocess.run
_ORIG_CHECK_OUTPUT = _real_subprocess.check_output
_ORIG_CALL = _real_subprocess.call
_ORIG_CHECK_CALL = _real_subprocess.check_call
_ORIG_POPEN = _real_subprocess.Popen


def _patched_run(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return _fake_completed_process(argv)
    return _ORIG_RUN(argv, *args, **kwargs)


def _patched_check_output(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return _fake_completed_process(argv).stdout
    return _ORIG_CHECK_OUTPUT(argv, *args, **kwargs)


def _patched_call(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return 0
    return _ORIG_CALL(argv, *args, **kwargs)


def _patched_check_call(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return 0
    return _ORIG_CHECK_CALL(argv, *args, **kwargs)


class _FakePopen:
    def __init__(self, argv, *args, **kwargs):
        self._proc = _fake_completed_process(argv)
        self.returncode = self._proc.returncode

    def communicate(self, *args, **kwargs):
        return self._proc.stdout, self._proc.stderr

    def wait(self, *args, **kwargs):
        return self.returncode


def _patched_popen(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return _FakePopen(argv, *args, **kwargs)
    return _ORIG_POPEN(argv, *args, **kwargs)


def install_subprocess_patch() -> None:
    """Monkeypatch subprocess.{run,check_output,call,check_call,Popen} so a
    duty.py that shells `acp trade`/`acp wallet send-transaction`/`acp card
    issue` gets recorded here instead of spawning the real CLI. Everything
    else passes through unmodified. Idempotent — replay.py may call this more
    than once."""
    _real_subprocess.run = _patched_run
    _real_subprocess.check_output = _patched_check_output
    _real_subprocess.call = _patched_call
    _real_subprocess.check_call = _patched_check_call
    _real_subprocess.Popen = _patched_popen


install_subprocess_patch()
