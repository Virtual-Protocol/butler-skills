---
name: unknown-cmd
description: A fixture whose shell blocks call container commands that do not exist and leave programs undeclared.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"requires":{"bins":["acp","bevo-read"]}}}
---

## When to use

A fixture that calls commands the container lacks and runs programs it never declares.

## Before you start

Nothing to settle.

## Procedure

1. [FIXED] Fetch with programs requires.bins does not list:

   ```sh
   curl -s https://api.llama.fi/protocols | jq '.[0].name'
   ```

2. [FIXED] Pipe a read into a bevo command the container does not have:

   ```sh
   bevo-read me | bevo-frob
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
