"""bevo-contract-call duty — a timer-triggered recurring call to a fixed
contract/function (rebalance, claim, compound). See
skills/bevo-contract-call/SKILL.md for the full procedure this implements.

duty.py has no keccak: SELECTOR is baked once at duty-creation time (the
one-off procedure's node encodeFunctionData recipe), never computed here.
"""
import json
import os

import bevo

CHAIN_ID = int(os.environ.get("CHAIN_ID", "8453"))
CONTRACT = os.environ.get("CONTRACT", "")
FUNCTION = os.environ.get("FUNCTION", "")
ARGS = json.loads(os.environ.get("ARGS", "[]"))
VALUE_WEI = int(os.environ.get("VALUE_WEI", "0"))
SELECTOR = os.environ.get("SELECTOR", "")


def pad_word(value) -> str:
    """Zero-pad a single argument to a 32-byte word. Handles the common
    address / uint256 cases; anything else must be pre-encoded by the
    creation-time node recipe and passed as a hex string already 64 chars."""
    if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
        return value[2:].rjust(64, "0").lower()
    if isinstance(value, str) and value.startswith("0x"):
        return value[2:].rjust(64, "0").lower()
    if isinstance(value, int):
        return format(value, "x").rjust(64, "0")
    raise bevo.BevoError(f"cannot pad argument {value!r} — pre-encode it at creation time")


def build_calldata() -> str:
    if not SELECTOR or not SELECTOR.startswith("0x") or len(SELECTOR) != 10:
        raise bevo.BevoError("SELECTOR must be a precomputed 4-byte hex selector (0x + 8 hex chars)")
    words = "".join(pad_word(a) for a in ARGS)
    return SELECTOR + words


def main() -> None:
    for ev in bevo.events():
        if ev.get("kind") != "timer":
            continue

        tick_iso = ev.get("event", {}).get("firedAt") or ev.get("firedAt")
        if not tick_iso:
            continue

        calldata = build_calldata()

        # [FIXED] read-before-write: dry-run the exact calldata from the agent
        # wallet before filing anything.
        me = bevo.read("/me")
        agent_wallet = me.get("agentWalletAddress") if isinstance(me, dict) else None
        dry_run = bevo.rpc(
            CHAIN_ID,
            "eth_call",
            [{"from": agent_wallet, "to": CONTRACT, "data": calldata}, "latest"],
        )
        bevo.log(f"contract-call dry run tick={tick_iso} result={dry_run}")

        key = f"contractcall:{bevo.SERVICE_ID}:{tick_iso}"
        result = bevo.execute(
            to=CONTRACT,
            data=calldata,
            value=VALUE_WEI,
            chain_id=CHAIN_ID,
            idempotency_key=key,
        )
        status = result.get("status") if isinstance(result, dict) else None
        bevo.log(f"contract-call filed tick={tick_iso} status={status} key={key}")

        if status in ("accepted", "manual_signing_required"):
            bevo.notify(f"Filed a call to {FUNCTION} on {CONTRACT} — approve it in Approvals.")


if __name__ == "__main__":
    main()
