---
name: valid
description: A minimal, fully compliant skill fixture used to test that the validator passes clean input.
version: 1.0.0
metadata: {"openclaw":{"emoji":"✅","requires":{"bins":["bevo-read"]}},"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false,"keywords":["fixture"],"requires":{"routes":["GET /butler-read/me"],"features":[],"gates":[],"bins":["bevo-read"]},"params":[{"name":"GREETING","type":"string","default":"hello"}]}}
---

## When to use

A fixture skill that always passes validation.

## Before you start

Nothing to resolve.

## Customize

- `GREETING` (default "hello") — the word used in the report.

## One-off procedure

1. [FIXED] Read the owner's identity:

   ```bash
   bevo-read me
   ```

2. [ADAPT] Say `GREETING` back to the owner.

## Duty procedure

Not applicable — this skill has no duty mode.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any error | Report it and stop. |

## Limits

Fixture only — never installed on a real container.

## Say to the owner

"GREETING, this is a fixture."
