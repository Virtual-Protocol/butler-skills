"""bevo-copytrade duty — mirrors LEADER's spot buys, one trade per leader
event, never twice. Sells are not mirrored in this version. See
skills/bevo-copytrade/SKILL.md for the full procedure this code implements.
"""
import json
import os

import bevo

STATE_PATH = "state.json"
MAX_SEEN = 2000
NOTIFIED_MANUAL_PATH = "notified_manual.json"

LEADER = os.environ.get("LEADER", "")
COPY_USDC_PER_TRADE = float(os.environ.get("COPY_USDC_PER_TRADE", "25"))
COPY_MAX_USDC = float(os.environ.get("COPY_MAX_USDC", "50"))
COPY_RATIO = float(os.environ.get("COPY_RATIO", "0"))
CHAIN_IDS = json.loads(os.environ.get("CHAIN_IDS", "[8453]"))


def load_json(path: str, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def size_for(event: dict) -> float:
    if COPY_RATIO > 0:
        leader_usd = event.get("usdValue") or 0
        size = float(leader_usd) * COPY_RATIO
    else:
        size = COPY_USDC_PER_TRADE
    return min(size, COPY_MAX_USDC)


class SeenSet:
    """A bounded, insertion-ordered seen-set backed by a list on disk. A
    plain Python set has no order, so trimming it with `[-MAX_SEEN:]` drops
    arbitrary ids, not the oldest — this keeps the list as the order of
    record and a parallel set only for O(1) membership checks."""

    def __init__(self, path: str, key: str, max_len: int = MAX_SEEN):
        self.path = path
        self.key = key
        self.max_len = max_len
        data = load_json(path, {key: []})
        self.items: list = list(data.get(key, []))
        self.member = set(self.items)

    def __contains__(self, item) -> bool:
        return item in self.member

    def add_and_save(self, item) -> None:
        if item in self.member:
            return
        self.items.append(item)
        self.member.add(item)
        if len(self.items) > self.max_len:
            dropped = self.items[: len(self.items) - self.max_len]
            self.items = self.items[-self.max_len :]
            self.member.difference_update(dropped)
        save_json(self.path, {self.key: self.items})


def main() -> None:
    handled = SeenSet(STATE_PATH, "handled")
    notified_manual = SeenSet(NOTIFIED_MANUAL_PATH, "ids")

    for ev in bevo.events():
        if ev.get("kind") != "trade":
            continue

        e = ev.get("event", {})
        event_id = e.get("id")
        if event_id is None or event_id in handled:
            continue

        if e.get("direction") != "buy":
            handled.add_and_save(event_id)
            continue

        chain_id = e.get("chainId")
        token_out = e.get("tokenOutAddress")
        usd_value = e.get("usdValue")
        if chain_id is None or token_out is None or usd_value is None:
            handled.add_and_save(event_id)
            continue

        try:
            chain_id_int = int(chain_id)
        except (TypeError, ValueError):
            handled.add_and_save(event_id)
            continue

        if chain_id_int not in CHAIN_IDS:
            handled.add_and_save(event_id)
            continue

        amount = size_for(e)
        key = f"copytrade:{bevo.SERVICE_ID}:{event_id}"

        command = (
            f"acp trade --token-in usdc --amount-in {amount} "
            f"--token-out {token_out} --chain-out {chain_id_int} "
            f"--idempotency-key {key}"
        )
        result = bevo.trade(command=command, idempotency_key=key)
        status = result.get("status") if isinstance(result, dict) else None
        bevo.log(f"copytrade event={event_id} status={status} key={key}")

        if status == "manual_signing_required" and event_id not in notified_manual:
            bevo.notify(f"Copied {LEADER}'s buy of {token_out} — approve it in Approvals.")
            notified_manual.add_and_save(event_id)

        # unknown_outcome and every other terminal status: never loop on it,
        # just mark the event handled and move on.
        handled.add_and_save(event_id)


if __name__ == "__main__":
    main()
