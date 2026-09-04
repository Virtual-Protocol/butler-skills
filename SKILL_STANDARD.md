# SKILL_STANDARD.md — the normative format

This file mirrors exactly what `scripts/validate.py` enforces. If code and doc disagree,
the code is right. See [README.md](README.md) for the how-to; this is the checklist.

## Layout

A skill is its own git repository (start from `Virtual-Protocol/butler-skill-template`),
with the skill files at the **repository root**:

```
SKILL.md        required
duty.py         optional (required when modes includes "duty")
CHANGELOG.md    required
```

The registry pins that repository as a git submodule at `skills/<name>`, at the commit of
its `v<version>` tag, so inside a registry checkout the same files appear as
`skills/<name>/SKILL.md` etc. The validator runs on either: `scripts/validate.py
--standalone <dir>` on a skill repo checkout (name from the frontmatter), `scripts/validate.py
skills/<name>` / `--all` on the registry's pinned submodules.

### Tree rules (enforced on both sides — the container refuses a clone that breaks them)

| Rule | Limit |
| --- | --- |
| Regular files in the tree | <= 50 (`.git` and `__pycache__` excluded) |
| Total bytes | <= 1 MB (and the 200 KB bundle rule below) |
| Symlinks | none, anywhere |
| Nested repositories / submodules | none — no `.gitmodules`, no `.git` below the top level |

### Pin rules (registry only — `scripts/check_pins.py`, run by CI)

| Rule | Check |
| --- | --- |
| Submodule path | exactly `skills/<frontmatter name>` |
| Submodule URL | `https://github.com/<owner>/<repo>` — never ssh, another host, or a local path |
| Pinned commit | carries tag `v<version>` in the skill repo (`git -C skills/<name> tag --points-at HEAD`) |
| Initialised | the checkout is present (`git submodule update --init --recursive`) |

## Frontmatter

OpenClaw-compatible: a `---` fence, `key: value` lines, `metadata` is **one line** of valid
JSON (the openclaw parser reads line-oriented, not a YAML block).

```yaml
---
name: butler-copytrade
description: Copy another member's buys once or as a standing duty, one trade per leader event, never twice. Use for "copy/mirror/follow <@handle or wallet>".
version: 1.0.1
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"bevo":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","mirror wallet","follow trader"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who to copy"},{"name":"COPY_USDC_PER_TRADE","type":"usd","default":25,"min":2,"max":10000}],"dutyTemplate":"duty.py"}}
---
```

| Field | Rule |
| --- | --- |
| `name` | `^[a-z0-9][a-z0-9-]{1,63}$`, not in `schema/reserved-names.json`. The `butler-` prefix is maintainer-only (`--maintainer` / `MAINTAINER=1`); the `bevo-` prefix is **refused** — it is the container's bundled-skill namespace (`bevo-hub`, `bevo-onchain`, `bevo-automation-creator`, …). Registry mode: equals the submodule directory name `skills/<name>`; `--standalone` mode: the pattern alone (the directory can be anything) |
| `description` | required, <= 160 chars, no wallet addresses, no override-phrase language |
| `version` | semver `X.Y.Z`, bumped whenever the skill changes; the skill repo is tagged `v<version>` and the registry pins that tag's commit |
| `metadata.openclaw` | only `emoji`, `homepage`, `requires.bins` allowed — no `always`, `install`, `requires.env`, `primaryEnv`, `os`, `disable-model-invocation` |
| `metadata.bevo.tier` | `core` \| `on-demand` |
| `metadata.bevo.modes` | subset of `["one-off","duty"]`, non-empty |
| `metadata.bevo.moneyMoving` | bool |
| `metadata.bevo.params` | see Params below |
| `metadata.bevo.requires.routes` | each matches `^(GET\|POST\|PATCH\|DELETE) /butler-(read\|exec)/[A-Za-z0-9/_:.-]+$` |
| `metadata.bevo.requires.gates` | subset of `canPerp, canSwap, canStock, canFiat, canOnramp` |
| `metadata.bevo.web3` | required when the body contains a `send-transaction` / `bevo.execute` line; `contracts` may be `[]` or omitted for a skill that takes the contract address as an owner-supplied param (then no `## Contracts` section is needed) |

## Body sections, in this exact order

`## When to use` · `## Before you start` · `## Customize` · `## One-off procedure` ·
`## Duty procedure` (required when `modes` includes `duty`) · `## Idempotency and retries`
(**mandatory when `moneyMoving:true`**, must contain the phrase "do not re-run") ·
`## Failure handling` · `## Limits` · `## Say to the owner`.

Web3 skills that list `metadata.bevo.web3.contracts` additionally carry a `## Contracts`
section (rendered from that block so the constants cannot drift from the frontmatter); an
empty `contracts` list needs none.

**A skill is the delta over AGENTS.md.** The container already teaches its agent the
command grammar, the money/safety invariants, the budgets and the routing (AGENTS.md and
the bundled skills). Never restate them in a skill — write only what is specific to this
task: the reads, the exact command shape with its key, the knobs, the skill's own failure
rows and limits. If you must point at a container rule, cite the AGENTS.md section rather
than paraphrasing it. `## Idempotency and retries` is the key formula plus "any error or
uncertainty: `bevo-read request <key>` first — do not re-run"; `## Failure handling` holds
only this skill's rows; `## Limits` is this skill's scope, never the container's global
rules.

Body <= 12,000 chars total. Bundle (all files in the skill directory, `.git` and
`__pycache__` excluded) <= 200 KB, no binary files; plus the tree rules above (<= 50
files, <= 1 MB, no symlinks, no nested submodules).

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

- Every listed `metadata.bevo.web3.contracts[].functions[].selector` is recomputed from its
  `signature` with viem (`scripts/check_selectors.mjs`) and must match (nothing to check
  when `contracts` is empty).
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
- Every `.gitmodules` URL in the registry is an `https://github.com/<owner>/<repo>` URL
  (`tests/test_gitmodules.py`, `scripts/check_pins.py`).
- `scripts/build_index.py --dry-run` must succeed for the whole repo (every entry gets a
  `source` block: repo URL, the 40-hex pinned commit, `ref: v<version>`).
- DCO sign-off required on every commit in the registry PR.
- Hub tooling downloaded for local validation (`validate.py`, `replay.py`, `stub_bevo.py`,
  `check_selectors.mjs`) is never part of a skill; the validator warns when it sees one in
  the tree (the template's `.gitignore` lists them).

## Local validation

`scripts/validate.py` is a single-file, stdlib-only tool and is published, with the replay
harness, at `https://virtual-protocol.github.io/butler-skills/tools/` (`scripts/publish_tools.py`
lays it out). A skill repo needs no registry checkout:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page
```

`replay.py` downloads `stub_bevo.py` and any fixture it needs from the same site when they
are not beside it. In CI the same two checks are the composite action
`uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`.

## The Butler toolbox

See README.md §3 for the full table (chat command / duty SDK call / server route / limits)
— it is reproduced there verbatim and is the source the command allowlist above is
generated from.
