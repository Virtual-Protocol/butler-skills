---
name: bad-frontmatter
description: Fixture with metadata that is not valid single-line JSON — must fail validation.
version: 1.0.0
metadata: {"openclaw": {"emoji": "x"}, "butler": {"tier": "on-demand", "modes": ["one-off"],}}
---

## When to use

Fixture only — the metadata line above has a trailing comma and is not valid JSON.

## Before you start

Nothing.

## Customize

Nothing.

## One-off procedure

1. [FIXED] Nothing.

## Duty procedure

Not applicable.

## Failure handling

| Outcome | What to do |
| --- | --- |
| Any | Fixture only. |

## Limits

Fixture only.

## Say to the owner

"Fixture."
