---
name: bins-undeclared
description: A fixture that runs commands its metadata does not declare.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"requires":{"bins":["bevo-notify"]}}}
---

## When to use

A fixture whose requires.bins is short of what its procedure runs.

## Before you start

Nothing to settle.

## Procedure

1. [FIXED] Get a number for a sign-in code:

   ```sh
   bevo-sms number
   ```

2. [FIXED] Read the owner's identity, then tell them:

   ```sh
   bevo-read me
   bevo-notify "Signed in"
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

"Signed in."
