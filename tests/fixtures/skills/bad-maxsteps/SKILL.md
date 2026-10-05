---
name: bad-maxsteps
description: A fixture that asks for fewer agent steps than a butler turn already gets.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"maxSteps":10,"requires":{"bins":["bevo-read"]}}}
---

## When to use

A fixture whose maxSteps is below the floor.

## Before you start

Nothing to settle.

## Procedure

1. [FIXED] Read the owner's identity:

   ```sh
   bevo-read me
   ```

## Idempotency and retries

A read is safe to repeat.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any error | Report it and stop. |

## Limits

Fixture only.

## Say to the owner

"Hello."
