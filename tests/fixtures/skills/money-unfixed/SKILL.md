---
name: money-unfixed
description: A fixture that places a trade from an [ADAPT] step, where the model may reshape it.
version: 1.0.0
metadata: {"butler":{"moneyMoving":true,"keywords":["fixture"],"requires":{"bins":["acp"]}}}
---

## When to use

A fixture whose money command is not pinned by a [FIXED] step.

## Before you start

Settle the token and the amount with your owner.

## Procedure

1. [ADAPT] Place the buy, however seems best:

   ```sh
   acp trade --token-in usdc --amount-in <USD> --token-out <TICKER> --idempotency-key <KEY> --json
   ```

## Idempotency and retries

One request, one key — do not re-run the trade on an error.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any error | Report it and stop. |

## Limits

Fixture only.

## Say to the owner

"Your buy is filed."
