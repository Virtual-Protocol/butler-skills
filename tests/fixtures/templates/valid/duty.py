import json
import os
import subprocess

import bevo

PARAMS = json.loads(os.environ.get("PARAMS", "{}"))
AMOUNT_USD = str(PARAMS.get("AMOUNT_USD", 5))


def main():
    for trade in bevo.trades():
        if trade.get("direction") != "buy":
            continue
        token_out = trade.get("tokenOutAddress") or trade.get("tokenOutSymbol")
        key = f"copy:{bevo.SERVICE_ID}:trade:{trade.get('id')}"
        subprocess.run(
            ["acp", "trade", "--token-in", "usdc", "--amount-in", AMOUNT_USD,
             "--token-out", token_out, "--idempotency-key", key],
            capture_output=True, text=True, timeout=180, check=False,
        )
        bevo.log(f"copied trade {trade.get('id')} for {AMOUNT_USD}")


if __name__ == "__main__":
    main()
