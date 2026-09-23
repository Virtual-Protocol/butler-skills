---
name: bad-requires
description: A fixture whose requires.skills names itself, a name Mastra would drop, and six skills.
version: 1.0.0
metadata: {"butler":{"moneyMoving":false,"keywords":["fixture"],"requires":{"bins":["bevo-read"],"skills":["valid","bad-requires","Not_A_Skill","one","two","three"]}}}
---

## When to use

A fixture whose requires.skills breaks the rules for one.

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
