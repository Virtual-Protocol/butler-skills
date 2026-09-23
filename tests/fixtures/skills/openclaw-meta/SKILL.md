---
name: openclaw-meta
description: A fixture still carrying the retired runtime's metadata block and fields.
version: 1.0.0
metadata: {"openclaw":{"emoji":"x","requires":{"bins":["bevo-read"]}},"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false,"keywords":["fixture"],"params":[{"name":"GREETING","type":"string"}],"requires":{"routes":["GET /butler-read/me"],"bins":["bevo-read"]},"web3":{"chains":[8453],"contracts":[]}}}
---

## When to use

A fixture with an OpenClaw-era metadata line.

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
