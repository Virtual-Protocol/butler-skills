import json
import os
import subprocess

import bevo

PARAMS = json.loads(os.environ.get("PARAMS", "{}"))
AMOUNT_USD = str(PARAMS.get("AMOUNT_USD", 5))


def main():
    # A waiter yields a TYPED event, exactly as the container's SDK does —
    # attributes, not dict keys, and snake_case rather than the wire's camel.
    for trade in bevo.trades():
        if trade.direction != "buy":
            continue
        token_out = trade.token_out_address or trade.token_out
        key = f"copy:{bevo.SERVICE_ID}:trade:{trade.id}"
        subprocess.run(
            ["acp", "trade", "--token-in", "usdc", "--amount-in", AMOUNT_USD,
             "--token-out", token_out, "--idempotency-key", key],
            capture_output=True, text=True, timeout=180, check=False,
        )
        bevo.log(f"copied trade {trade.id} for {AMOUNT_USD}")


if __name__ == "__main__":
    main()
