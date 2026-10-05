import bevo


def main():
    for tick in bevo.ticks():
        # BUG (deliberate, for the validator fixture suite): bevo.trade() was
        # retired on 2026-09-21 — a duty spends by shelling `acp trade` itself.
        bevo.trade(command="acp trade --token-in usdc --amount-in 5 --token-out VIRTUAL")


if __name__ == "__main__":
    main()
