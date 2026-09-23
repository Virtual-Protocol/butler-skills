---
name: unknown-cmd
description: A fixture whose shell blocks run commands a skill may not run.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"requires":{"bins":["acp","bevo-read"]}}}
---

## When to use

A fixture that reaches past the command allowlist.

## Before you start

Nothing to settle.

## Procedure

1. [FIXED] Read something the hard way:

   ```sh
   curl -s butler-read.internal/me
   ```

2. [FIXED] Pipe a read into a command nobody listed:

   ```sh
   bevo-read me | jq .username
   ```

3. [ADAPT] Use a read that does not exist, and a group the wrapper refuses:

   ```sh
   bevo-read frobnicate
   acp configure
   ```

## Idempotency and retries

Reads are safe to repeat.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any error | Report it and stop. |

## Limits

Fixture only.

## Say to the owner

"Done."
