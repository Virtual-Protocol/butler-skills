import os
import subprocess

import bevo

AMOUNT_USD = os.environ.get("AMOUNT_USD", "5")
KEY_FLAG = "--idempotency-key"  # built from a name, not a literal in the call itself


def main():
    for tick in bevo.ticks():
        subprocess.run(
            ["acp", "trade", "--token-in", "usdc", "--amount-in", AMOUNT_USD,
             "--token-out", "VIRTUAL", KEY_FLAG, f"buy:{bevo.SERVICE_ID}:{tick.get('at')}"],
            capture_output=True, text=True, timeout=180, check=False,
        )


if __name__ == "__main__":
    main()
