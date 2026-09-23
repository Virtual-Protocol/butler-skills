---
name: valid
description: A minimal, fully compliant skill fixture — buys one token once for the owner and reports the fill.
version: 1.0.0
metadata: {"butler":{"moneyMoving":true,"keywords":["fixture","buy once"],"requires":{"bins":["acp","bevo-read","bevo-notify"]}}}
---

## When to use

Your owner asks for one buy of one token, right now. A fixture: the hub's own tests
use it to prove that a compliant skill passes.

## Before you start

- Settle the token and the dollar amount with your owner before anything runs.
- Read what they hold first:

```sh
bevo-read assets
```

## Procedure

1. [ADAPT] Confirm the token and the amount, in your owner's words.
2. [FIXED] Place the buy once, with one key for this request:

   ```sh
   acp trade --token-in usdc --amount-in <USD> --token-out <TICKER> --idempotency-key <KEY> --json
   ```

3. [FIXED] Tell your owner what was filed:

   ```sh
   bevo-notify "Bought <TICKER> for <USD> USDC"
   ```

### Sizing

`references/sizing.md` has the minimums.

## Idempotency and retries

One request, one key. On an error or an unknown outcome, look the key up — do not
re-run the trade:

```sh
bevo-read request <KEY> --route trade
```

## Failure handling

| Outcome | What to do |
| --- | --- |
| Rejected | Report the reason and stop. |
| Unknown | Look the key up; never file it again. |

## Limits

One token, one buy, one request. A standing order is a duty, not this skill.

## Say to the owner

"Your buy of <TICKER> is filed — it is waiting in your Approvals."
