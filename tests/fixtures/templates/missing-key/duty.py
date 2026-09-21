import subprocess

import bevo


def main():
    for tick in bevo.ticks():
        # BUG (deliberate, for the validator fixture suite): fully literal
        # argv with no --idempotency-key anywhere in it.
        subprocess.run(
            ["acp", "trade", "--token-in", "usdc", "--amount-in", "5", "--token-out", "VIRTUAL"],
            capture_output=True, text=True, timeout=180, check=False,
        )


if __name__ == "__main__":
    main()
