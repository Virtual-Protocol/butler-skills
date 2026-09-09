---
name: banned-command
description: Fixture whose procedure uses a raw curl and a bare acp --help — must fail the command allowlist check.
version: 1.0.0
metadata: {"openclaw":{"emoji":"🚫"},"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false,"keywords":["fixture"],"requires":{"routes":[],"features":[],"gates":[],"bins":[]},"params":[]}}
---

## When to use

Fixture only.

## Before you start

Nothing.

## Customize

Nothing.

## One-off procedure

1. [FIXED] Read something the hard way:

   ```bash
   curl {API_BASE}/butler-read/me
   ```

2. [FIXED] Explore the CLI the forbidden way:

   ```bash
   acp --help
   ```

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
