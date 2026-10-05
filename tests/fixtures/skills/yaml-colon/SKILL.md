---
name: yaml-colon
description: Read the owner's holdings: an unquoted colon and space, which YAML reads as a mapping.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"requires":{"bins":["bevo-read"]}}}
---

## When to use

A fixture whose description breaks gray-matter, so Mastra would drop it silently.

## Before you start

Nothing to settle.

## Procedure

1. [FIXED] Read the holdings:

   ```sh
   bevo-read assets
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

"Here is what you hold."
