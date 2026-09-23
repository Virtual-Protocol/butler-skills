---
name: requires-skill
description: A fixture that builds on the valid fixture skill and takes a longer turn to do it.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture","builds on a skill"],"maxSteps":150,"requires":{"bins":["bevo-read"],"skills":["valid"]}}}
---

## When to use

A fixture that leans on another skill: the `valid` fixture skill, which the butler
installs first. A turn that loads it may take up to 150 steps.

## Before you start

Settle what your owner wants read before anything runs.

## Procedure

1. [FIXED] Read the owner's holdings:

   ```sh
   bevo-read assets
   ```

2. [ADAPT] Hand the buy itself to the `valid` skill's procedure.

## Idempotency and retries

Reads are safe to repeat.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any error | Report it and stop. |

## Limits

Fixture only.

## Say to the owner

"Here is what you hold."
