# SKILL_STANDARD.md — the normative format

This file mirrors exactly what `scripts/validate.py` enforces. If code and doc disagree,
the code is right. See [README.md](README.md) for the how-to; this is the checklist.

## Layout

```
skills/<name>/SKILL.md        required
skills/<name>/duty.py         optional (required when modes includes "duty")
skills/<name>/CHANGELOG.md    required
```

## Frontmatter

OpenClaw-compatible: a `---` fence, `key: value` lines, `metadata` is **one line** of valid
JSON (the openclaw parser reads line-oriented, not a YAML block).

```yaml
---
name: bevo-copytrade
description: Copy another member's buys once or as a standing duty, one trade per leader event, never twice. Use for "copy/mirror/follow <@handle or wallet>".
version: 1.0.0
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"bevo":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","mirror wallet","follow trader"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who to copy"},{"name":"COPY_USDC_PER_TRADE","type":"usd","default":25,"min":2,"max":10000}],"dutyTemplate":"duty.py"}}
---
```

| Field | Rule |
| --- | --- |
| `name` | `^[a-z0-9][a-z0-9-]{1,63}$`, equals the directory name, not in `schema/reserved-names.json`, `bevo-` prefix is maintainer-only |
| `description` | required, <= 160 chars, no wallet addresses, no override-phrase language |
| `version` | semver `X.Y.Z`, bumped whenever the directory changes |
| `metadata.openclaw` | only `emoji`, `homepage`, `requires.bins` allowed — no `always`, `install`, `requires.env`, `primaryEnv`, `os`, `disable-model-invocation` |
| `metadata.bevo.tier` | `core` \| `on-demand` |
| `metadata.bevo.modes` | subset of `["one-off","duty"]`, non-empty |
| `metadata.bevo.moneyMoving` | bool |
| `metadata.bevo.params` | see Params below |
| `metadata.bevo.requires.routes` | each matches `^(GET\|POST\|PATCH\|DELETE) /butler-(read\|exec)/[A-Za-z0-9/_:.-]+$` |
| `metadata.bevo.requires.gates` | subset of `canPerp, canSwap, canStock, canFiat, canOnramp` |
| `metadata.bevo.web3` | required when the body contains a `send-transaction` / `bevo.execute` line |

## Body sections, in this exact order

`## When to use` · `## Before you start` · `## Customize` · `## One-off procedure` ·
`## Duty procedure` (required when `modes` includes `duty`) · `## Idempotency and retries`
(**mandatory when `moneyMoving:true`**, must contain the phrase "do not re-run") ·
`## Failure handling` · `## Limits` · `## Say to the owner`.

Web3 skills additionally carry a `## Contracts` section (rendered from `metadata.bevo.web3`
so the constants cannot drift from the frontmatter).

Body <= 12,000 chars total. Bundle (all files in the skill directory) <= 200 KB, no binary
files.

## Params

Each entry: `name` (`^[A-Z][A-Z0-9_]*$`, unique), `type` (`usd | number | int | enum |
chainIds | chainId | address | principalId | wallet | principalId|wallet | string | bool`),
`default`, `min`/`max` or `values`, `required`, `ask` (mandatory when `required: true`),
`help`. Defaults must fall inside `min`/`max`.

## Numbered steps

Every numbered step in `## One-off procedure` and `## Duty procedure` carries `[FIXED]` or
`[ADAPT]`. Any line containing `acp trade`, `acp wallet send-transaction`, or `bevo-send`
must sit inside a `[FIXED]` step.

## Command allowlist

Every command line inside a fenced (```` ``` ````) shell code block must start with one of:
`bevo-notify`, `bevo-rpc`, `bevo-read`, `bevo-send`, `acp`, `bevo-automation`, `bevo-hub`,
`bevo-x`, `bevo-location`, `bevo-sms`, `web-checkout`, `node`. Raw `curl` is forbidden.
`bevo-read`/`bevo-automation`/`bevo-hub` subcommands are checked against their real shim
subcommand list; `acp <area>` subcommands are checked against the real `acp` areas; bare
`acp --help` is forbidden.

## `duty.py` conventions

`import bevo` plus stdlib only.

- Forbidden: `from bevo import ...`, `import bevo as ...`, `subprocess`, `os.system`,
  `socket`, `urllib`, `requests`, `http.client`, `eval`, `exec`, bare `except: pass`.
- Every `bevo.trade(...)` / `bevo.execute(...)` call passes a keyword `idempotency_key=`
  that is not `None`.
- Every `os.environ[...]` / `os.environ.get(...)` key must be a declared `params` name.
- Must `py_compile` and parse as valid Python 3.11 AST.

## Web3 rules

- Every `metadata.bevo.web3.contracts[].functions[].selector` is recomputed from its
  `signature` with viem (`scripts/check_selectors.mjs`) and must match.
- Contract `address` is a checksummed `0x` + 40 hex chars, or a `{{PARAM}}` placeholder of
  type `address`.
- Every `acp wallet send-transaction` line carries `--chain-id`, `--to`, `--data`, and
  `--idempotency-key`.
- Every write step is preceded by a dry-run step (`bevo-rpc ... eth_call` / `bevo.rpc(...,
  "eth_call", ...)`).
- No raw RPC URL anywhere — reads go through `bevo-rpc` / `bevo.rpc()` only.

## Misc

- `CHANGELOG.md` required, one entry per version.
- No secrets (`brt_...`, `sk-...`, 64-hex strings, JWTs) anywhere in the file.
- No URLs except `{API_BASE}` and `github.com/Virtual-Protocol` / `raw.githubusercontent.com/Virtual-Protocol` links.
- No override-phrase language ("ignore", "override", "SOUL.md", "do not tell", a raw wallet
  address in `description`).
- `scripts/build_index.py --dry-run` must succeed for the whole repo.
- DCO sign-off required on every commit in the PR.

## The Butler toolbox

See README.md §3 for the full table (chat command / duty SDK call / server route / limits)
— it is reproduced there verbatim and is the source the command allowlist above is
generated from.
