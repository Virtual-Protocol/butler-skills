import os

import bevo

AMOUNT = os.environ.get("AMOUNT", "10")


def main():
    for ev in bevo.events():
        if ev.get("kind") != "trade":
            continue
        # BUG (deliberate, for the validator fixture suite): no idempotency_key kwarg.
        bevo.trade(command=f"acp trade --amount-in {AMOUNT}")


if __name__ == "__main__":
    main()
