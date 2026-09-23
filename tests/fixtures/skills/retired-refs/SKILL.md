---
name: retired-refs
description: A fixture whose prose still points at the retired runtime.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"requires":{"bins":["bevo-read"]}}}
---

## When to use

When a request fits — AGENTS.md § 7 names the rest.

## Before you start

Install the helper with bevo-hub first, the way OpenClaw did it.

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
