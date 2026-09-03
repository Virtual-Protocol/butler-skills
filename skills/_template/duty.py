"""duty.py scaffold — TODO: replace this docstring with what the duty does.

Conventions (enforced by scripts/validate.py):
  - `import bevo` + stdlib only. Never `from bevo import ...` or an alias.
  - Read configuration only from declared `params` via os.environ, with the
    declared defaults.
  - Every bevo.trade(...)/bevo.execute(...) call passes idempotency_key=
    derived from the source event id and bevo.SERVICE_ID.
  - Keep a bounded state.json seen-set in cwd (belt: server ledger, braces:
    local state).
  - No bare `except: pass`, no subprocess/os.system/eval/exec.
"""
import json
import os

import bevo

STATE_PATH = "state.json"
MAX_SEEN = 2000

# TODO: read your declared params with their defaults, e.g.:
# TODO_PARAM = os.environ.get("TODO_PARAM", "default-value")


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"handled": []}


def save_state(state: dict) -> None:
    handled = state.get("handled", [])[-MAX_SEEN:]
    state["handled"] = handled
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def main() -> None:
    state = load_state()
    handled = set(state.get("handled", []))

    for ev in bevo.events():
        # TODO: filter to the event kind(s) this duty cares about.
        kind = ev.get("kind")
        if kind != "TODO":
            continue

        event = ev.get("event", {})
        event_id = event.get("id")
        if event_id is None or event_id in handled:
            continue

        # TODO: build the idempotency key and call bevo.trade / bevo.execute.
        key = f"_template:{bevo.SERVICE_ID}:{event_id}"
        bevo.log(f"TODO handling event {event_id} with key {key}")

        handled.add(event_id)
        state["handled"] = list(handled)
        save_state(state)


if __name__ == "__main__":
    main()
