---
name: bevo-copytrade
description: Copy another member's buys once or as a standing duty, one trade per leader event, never twice. Use for "copy/mirror/follow <@handle or wallet>".
version: 1.0.0
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"bevo":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","mirror wallet","follow trader"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who to copy"},{"name":"COPY_USDC_PER_TRADE","type":"usd","default":25,"min":2,"max":10000},{"name":"COPY_MAX_USDC","type":"usd","default":50,"min":2,"max":10000},{"name":"COPY_RATIO","type":"number","default":0,"min":0,"max":1,"help":"share of the leader's USD size; 0 = fixed size"},{"name":"CHAIN_IDS","type":"chainIds","default":[8453]}],"dutyTemplate":"duty.py"}}
---

## When to use

The owner asks to copy, mirror or follow another member's or wallet's buys — a one-time
"copy their last buy" or a standing "copy every buy they make". Spot buys only in v1; never
a substitute for a plain trade the owner sizes themselves.

## Before you start

Resolve the leader:

```bash
bevo-read user <@handle>
```

On `user_not_found` (hidden handle or no shared group) ask the owner for a wallet address
instead and use `wallets:[...]` in the trade-activity read below. Confirm the `canSwap` gate
is available (the install already checked this). Get the owner's sizing rule in their own
words (fixed USD per copy, or a ratio of the leader's size, and a daily cap).

## Customize

- `LEADER` (required, asked as "who should I copy?") — the principalId or wallet to mirror.
- `COPY_USDC_PER_TRADE` (default $25) — fixed USD size per copy when `COPY_RATIO` is 0.
- `COPY_MAX_USDC` (default $50) — hard per-trade ceiling regardless of ratio.
- `COPY_RATIO` (default 0) — when > 0, size = leader's USD size * ratio, clamped to
  `COPY_MAX_USDC`.
- `CHAIN_IDS` (default `[8453]`) — only leader buys on one of these chains are copied.

`[ADAPT]` steps: which event the owner meant, the sizing rule in the owner's words, an
optional `judgment` filter, notification wording. `[FIXED]` steps: leader resolution, the
newest-first read, null-field skips, the exact trade command shape, the idempotency key, the
report to the owner.

## One-off procedure

1. [FIXED] Read the leader's recent activity (newest-first):

   ```bash
   bevo-read trade-activity --principal-ids <leaderPrincipalId> --limit 20
   ```

2. [ADAPT] Pick the event the owner meant — default to the newest event with
   `direction:"buy"`.
3. [FIXED] Skip the event if `direction`, `chainId`, `tokenOutAddress` or `usdValue` is null,
   or if `chainId` (cast to int) is not in `CHAIN_IDS` — pick the next candidate instead.
4. [ADAPT] Compute your size from `COPY_USDC_PER_TRADE` / `COPY_RATIO`, clamped to
   `COPY_MAX_USDC`.
5. [FIXED] Echo back to the owner: token address, chain, the leader's size, and your size,
   before filing anything.
6. [FIXED] File the trade with a key derived from the event id — token is always the
   address, never a symbol; this is a buy so `--chain-out`, never `--chain-in`:

   ```bash
   acp trade --token-in usdc --amount-in <usd> --token-out <tokenOutAddress> --chain-out <chainId> --idempotency-key copytrade:chat:<eventId>
   ```

7. [FIXED] On `accepted` or `manual_signing_required`, stop and report. On a network error
   or timeout, follow "Idempotency and retries" below — do not re-issue the command
   yourself.

## Duty procedure

1. [ADAPT] Confirm the trigger is what the owner meant: every buy by `LEADER`, on the
   allowed chains, sized by the params above.
2. [FIXED] Trigger JSON: `{"kind":"trade","principalIds":["<leaderPrincipalId>"],"direction":"buy"}`.
3. [FIXED] `env` = the six params above (skill defaults, then any `bevo-hub set` prefs the
   owner saved, then this ask's own values).
4. [ADAPT] `requestedDailyLimitUsdc` = the owner's stated daily cap; `yardstick` = "Every buy
   by LEADER is mirrored once, within a minute, for $COPY_USDC_PER_TRADE, never twice."
5. [FIXED] Create it — never hand-write the duty's code yourself, the shim loads `duty.py`
   from this skill:

   ```bash
   bevo-automation create --from-skill bevo-copytrade@1.0.0 '<json>'
   ```

6. [FIXED] Say to the owner: "Created, pending — arm it in Approvals; the card proposes
   $requestedDailyLimitUsdc/day as its pocket; nothing runs until then; inside the pocket
   copies run hands-free, above it they become signing requests."

## Idempotency and retries

Key formula: `copytrade:chat:<eventId>` for a one-off, `copytrade:{SERVICE_ID}:<eventId>` for
a duty (see `duty.py`). Pass the key on every `acp trade` / `bevo.trade(...)` call for this
skill, never derive it from a timestamp.

- `accepted` — the trade filed; report it and stop. Do not re-run it.
- Network error / timeout before a response — do not re-run; poll
  `bevo-read request <key>` first; only resend if it reports nothing was claimed.
- `IDEMPOTENT_IN_FLIGHT` — the SDK/shim already polls status for you; do not re-issue the
  command.
- `IDEMPOTENCY_KEY_REUSED` — a different set of params already used this exact key; log it
  and do not trade.
- `IDEMPOTENT_UNKNOWN_OUTCOME` — log it, tell the owner the outcome is unknown, and do not
  re-run — this is the one outcome that never resolves itself.

## Failure handling

| Outcome | What to do |
| --- | --- |
| `accepted` | Done — report token, chain and size. |
| `manual_signing_required` | Notify the owner once; do not poll in a loop. |
| `IDEMPOTENCY_KEY_REUSED` | Log only, do not trade — another run already handled this event. |
| `IDEMPOTENT_IN_FLIGHT` | Wait for the status poll; do not resend. |
| `IDEMPOTENT_UNKNOWN_OUTCOME` | Log; never retry; tell the owner status is unknown. |
| 4xx refusal | Log the code; do not resize and retry on your own judgment. |
| Pocket short | The server files a top-up card; continue, do not cancel the flow. |

## Limits

Buys only in v1. Sells are not mirrored in this version. Perps are never copied. Yanking
this skill from the hub does not stop a duty already created from it — the duty keeps its
own copy of `duty.py`.

## Say to the owner

One-off: "Copied LEADER's buy of `<amount>` USD into `<token>` on chain `<chainId>`. Sells
are not mirrored in this version." Duty creation: "Created, pending — arm it in Approvals;
the card proposes the daily cap as its pocket; nothing runs until then."
