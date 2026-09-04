---
name: undeclared-param
description: Fixture whose duty.py reads an environment key that is not declared in params — must fail validation.
version: 1.0.0
metadata: {"openclaw":{"emoji":"❓"},"butler":{"tier":"on-demand","modes":["duty"],"moneyMoving":false,"keywords":["fixture"],"requires":{"routes":[],"features":[],"gates":[],"bins":[]},"params":[{"name":"KNOWN_PARAM","type":"string","default":"x"}]}}
---

## When to use

Fixture only.

## Before you start

Nothing.

## Customize

- `KNOWN_PARAM` (default "x").

## One-off procedure

1. [FIXED] Not used — duty-only fixture.

## Duty procedure

1. [FIXED] Trigger JSON: `{"kind":"timer","intervalSeconds":3600}`.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any | Fixture only. |

## Limits

Fixture only.

## Say to the owner

"Fixture."
