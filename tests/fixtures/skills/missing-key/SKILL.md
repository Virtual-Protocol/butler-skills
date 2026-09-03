---
name: missing-key
description: Fixture with a duty.py that calls bevo.trade without an idempotency_key — must fail validation.
version: 1.0.0
metadata: {"openclaw":{"emoji":"❌","requires":{"bins":["acp"]}},"bevo":{"tier":"on-demand","modes":["duty"],"moneyMoving":true,"keywords":["fixture"],"requires":{"routes":["POST /butler-exec/trade"],"features":[],"gates":["canSwap"],"bins":["acp"]},"params":[{"name":"AMOUNT","type":"usd","default":10,"min":2,"max":100}]}}
---

## When to use

Fixture only.

## Before you start

Nothing to resolve.

## Customize

- `AMOUNT` (default $10).

## One-off procedure

1. [FIXED] Not used — duty-only fixture.

## Duty procedure

1. [FIXED] Trigger JSON: `{"kind":"timer","intervalSeconds":3600}`.
2. [FIXED] Create it:

   ```bash
   bevo-automation create --from-skill missing-key@1.0.0 '<json>'
   ```

## Idempotency and retries

This fixture deliberately violates its own rule — do not re-run this fixture in production.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any | Fixture only. |

## Limits

Fixture only.

## Say to the owner

"Fixture."
