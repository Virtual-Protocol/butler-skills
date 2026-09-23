# SKILL_STANDARD.md — the normative format for skills

This file mirrors exactly what `scripts/validate.py` enforces for a skill. If code and doc
disagree, the code is right. Duty templates have their own checklist,
[TEMPLATE_STANDARD.md](TEMPLATE_STANDARD.md); [README.md](README.md) is the how-to.

## What a skill is

A **skill** teaches the butler a capability: a `SKILL.md` playbook — when to use it, what
to settle first, the numbered procedure, what to say — that the butler installs **on
demand** when a request calls for it. On the butler, a skill lives in
`<workspace>/skills/<name>/`, where Mastra lists it among the agent's skills and the model
reads it for a turn. A skill is guidance for a conversation; a standing instruction that
runs unattended is a **duty template** instead.

Mastra reads `SKILL.md` with gray-matter (js-yaml underneath), and a skill whose
frontmatter fails to parse or validate is **dropped from discovery without a word** — it
looks installed and never appears. Most of the frontmatter rules below exist to make that
impossible.

## Layout

A skill is its own git repository, with these files at the **repository root**:

```
SKILL.md          required — the playbook: frontmatter + seven sections
README.md         required — for the humans maintaining the repo; never published
CHANGELOG.md      required — one entry per version; never published
references/       optional — more Markdown the playbook points at
```

**Published to butlers: `SKILL.md` and `references/**/*.md`, nothing else.** A reference
path is letters, digits, `.`, `_` and `-` segments that do not start with `.`; another
file under `references/` is a warning (not published), a `.md` path outside that shape
is an error. `scripts/` and `assets/` are warned about (not published). A `duty.py` is an
error — a program that runs unattended is a duty template. A repository holding both
`SKILL.md` and `recipe.json` is refused: a repo is one kind.

The registry keeps no copy of that repository. `skills.json` lists the skill by `name`,
`repo` (a GitHub link) and `ref` (a branch or a tag); `scripts/build_index.py`
shallow-clones each entry at its ref, validates it, and publishes the files above. The
validator runs on either: `scripts/validate.py --standalone <dir>` on a skill repo
checkout (name from the frontmatter), or `scripts/validate.py --all` in registry CI. It
tells a skill from a template by itself — there is no flag.

### Tree rules (the same as a template's)

| Rule | Limit |
| --- | --- |
| Regular files in the tree | <= 50 (`.git` and `__pycache__` excluded) |
| Total bytes | <= 1 MB |
| Symlinks | none, anywhere |
| Nested repositories / submodules | none — no `.gitmodules`, no `.git` below the top level |

### Registry listing rules (`scripts/check_registry.py`, run by CI)

| Rule | Check |
| --- | --- |
| `name` | Mastra's skill-name rule (below), listed once — and never also in `templates.json`; it must equal the repo's frontmatter `name` (`scripts/validate.py --all` clones into a directory named after the entry and compares) |
| `repo` | `https://github.com/<owner>/<repo>` — never ssh, another host, a local path, credentials, a query or a fragment |
| `ref` | a plain branch or tag name (`^[A-Za-z0-9._/-]{1,100}$`) that resolves on the remote (`--offline` skips that one check) |
| Order | the file is sorted by `name` |

Listing a skill is **maintainer-only** (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Frontmatter

A `---` line, `key: value` lines, a `---` line. Exactly four keys, each once, each on one
line — no YAML blocks, continuation lines or comments, no control characters:

```yaml
---
name: tip-once
description: Send one member a one-off tip in USDC when your owner asks, and say where it landed.
version: 1.0.0
metadata: {"butler":{"moneyMoving":true,"keywords":["tip","send a tip"],"requires":{"bins":["bevo-read","bevo-send"]}}}
---
```

| Field | Rule |
| --- | --- |
| `name` | Mastra's rule: `^[a-z0-9]+(-[a-z0-9]+)*$` (lowercase, digits, single hyphens, none leading or trailing), at most 64 characters, and not something YAML reads as a number or date (`123`, `1e5`, `2026-09-21`). In registry mode it must equal the directory — the `skills.json` entry, and the directory the butler installs it in. Not in `schema/reserved-names.json` (which includes `acp-cli` and `duty-code`, the two skills compiled into the butler image). The `butler-` prefix is maintainer-only (`--maintainer` / `MAINTAINER=1`); `bevo-` is **refused** — the container's bundled-command namespace |
| `description` | at most 200 characters, one line. Either a JSON double-quoted string (`description: "Read it: all of it"`) or a plain value YAML reads back verbatim: no `: `, no ` #`, not ending in `:`, not starting with any of `` - ? : , [ ] { } # & * ! \| > ' " % @ ` ``, and not a number, boolean, null or date. The validator's error spells out the quoted form to paste |
| `version` | semver `X.Y.Z`; bump it for every change — a published `name@version` never changes bytes |
| `metadata` | ONE line of JSON: `{"butler": {"moneyMoving": <bool>, "keywords": [<non-empty strings>], "requires": {"bins": [<commands>]}}}` — all three fields required; the only optional ones are `maxSteps` and `requires.skills` (below); no other keys anywhere, no duplicated JSON key |

The OpenClaw-era fields are refused by name, with where their content goes now:
`metadata.openclaw`, `metadata.butler.tier` / `modes` / `params` / `web3` / `dutyTemplate`,
and `metadata.butler.requires.routes` / `features` / `gates`. So are other Mastra keys
(`license`, `user-invocable`, …): the frontmatter is exactly the four above.

`requires.bins` lists every command the skill's shell blocks run (see Commands), and only
commands from the allowlist. A butler can check it has them before installing.

### Optional: `maxSteps` and `requires.skills`

```yaml
metadata: {"butler":{"moneyMoving":true,"keywords":["grabfood","order food"],"maxSteps":150,"requires":{"bins":["app-checkout","bevo-read"],"skills":["butler-app-checkout"]}}}
```

| Field | Rule |
| --- | --- |
| `maxSteps` | an integer from 20 to 500: how many agent steps a turn that loads this skill may take. A butler turn gets 20 by default; loading the skill raises that turn's budget to `maxSteps`, capped by the container's own ceiling (200 unless it is configured otherwise). Set it for a long errand — a phone checkout takes ~150 steps — and leave it out when the default is enough |
| `requires.skills` | at most 5 skills this one builds on — `butler-grabfood` (Grab's Food section) builds on `butler-app-checkout`. Each matches Mastra's skill-name rule (`^[a-z0-9]+(-[a-z0-9]+)*$`, at most 64 characters), is listed once, and is never the skill itself |

The butler's hub **installs a skill's required skills first**, **refuses to remove a skill
that another installed skill requires**, and **when a required skill is de-listed, takes the
skills that require it with it**. So a requirement must always be something the index
serves: `scripts/validate.py --all` refuses a requirement `skills.json` does not list, and
any cycle of requirements (there would be no order to install them in); the publish build
refuses the same, plus a requirement whose current version is yanked (it publishes only
the tombstone). `--standalone` cannot see the listing and does not check it.

Both reach the index row: `maxSteps` when the skill sets it (absent otherwise), and
`requires` as `{"bins": [...], "skills": [...]}` — `skills` always present, `[]` when none.

## Body

At most **12,000 characters**. Exactly these seven `##` sections, **in this order**; any
other material is a `###` subsection of one of them:

`## When to use` · `## Before you start` · `## Procedure` · `## Idempotency and retries` ·
`## Failure handling` · `## Limits` · `## Say to the owner`

The retired OpenClaw sections are refused with a pointer: `## One-off procedure` is now
`## Procedure`; `## Customize` folds into `## Before you start`; `## Duty procedure` is a
duty template, never a skill section.

### Procedure steps

- `## Procedure` has at least one numbered step, and **every** numbered step there carries
  `[FIXED]` (do exactly this) or `[ADAPT]` (shape it to the request). Numbered lists in
  other sections are prose.
- A **money command** — `acp trade`, `acp wallet send-transaction`, `acp card`,
  `bevo-send`, `app-checkout checkpoint` — may only appear in a shell block inside a
  `[FIXED]` step of `## Procedure`. The step is the numbered line above the block; any
  heading ends it. A money command anywhere else (another section, a later subsection, a
  reference file) is an error.
- A skill whose shell blocks run a money command must declare `"moneyMoving": true`.
- When `moneyMoving` is true, `## Idempotency and retries` must say **"do not re-run"** — a
  retried money command can spend twice; the answer to "did it run?" is
  `bevo-read request <key>`, never a second command.

## Commands

Every command line in a shell code block (```` ``` ```` or `~~~` fences whose info string
is empty, `sh`, `bash`, `shell`, `console`, `zsh` or `shell-session`) is checked — in
`SKILL.md` and in every published reference. A `$ ` prompt is stripped, `\` continuations
are joined, comments and heredoc bodies are skipped, and a line is split on `|`, `&&`,
`||`, `;` and `&`, so **every** command on it is checked, not just the first.

| Command | Allowed subcommands (argv[0], or the acp group) |
| --- | --- |
| `bevo-read` | `get`, `messages`, `channel-messages`, `participants`, `summary`, `search`, `groups`, `user`, `assets`, `me`, `policy`, `token-search`, `token`, `token-balance`, `token-price`, `stock-min`, `token-stats`, `trade-activity`, `trade-executions`, `wallet-transfers`, `request`, `card-budget` |
| `bevo-sms` | `number`, `status`, `otp` |
| `bevo-automation` | `create`, `validate`, `update`, `enable`, `disable`, `delete`, `list`, `show`, `logs` |
| `bevo-x` | `search` |
| `app-checkout` | `start`, `status`, `screen`, `shot`, `tap`, `type`, `key`, `swipe`, `wait`, `install`, `open`, `checkpoint`, `end` |
| `acp` | the groups the container's `acp` wrapper lets through: `agent` (only `whoami`, `list`, `use`, `link`, `generate-signer-key`, `signer-status`, `help`), `browse`, `card`, `chain`, `email`, `events`, `job`, `message`, `offering`, `policy`, `provider`, `resource`, `skill`, `subscription`, `trade`, `wallet`. `client`, `compute` and `configure` are refused; a bare `acp` / `acp --help` is refused |
| `bevo-send`, `bevo-rpc`, `bevo-notify` | any arguments |

Anything else is refused — in particular `curl`, `wget`, `node`, `python` (a skill reads
through `bevo-read` / `bevo-rpc` and runs no code of its own), a pipe into an unlisted
command, a `VAR=value` prefix, and command substitution (`$(…)`, backticks, `<(…)`).
Write alternatives in prose, never as `a|b` inside a command: that is a pipe.

The tables are read off virtuals-agent: `BUTLER_COMMANDS`
(`src/integrations/butler/launchers.ts`), each command's own dispatch in
`src/integrations/butler/bin/`, and the acp wrapper (`src/integrations/acp/wrapper.ts`).
A command the container gains is a change here first.

## Lints (every published file)

- No secrets: `brt_…`, `sk-…`, 64-hex strings, JWTs.
- No URLs except `https://github.com/Virtual-Protocol/…` and
  `https://raw.githubusercontent.com/Virtual-Protocol/…`.
- No invisible characters: zero-width (U+200B–U+200D, U+2060, U+FEFF), the bidi overrides
  and isolates (U+202A–U+202E, U+2066–U+2069), U+2028/U+2029.
- No raw `0x` + 40-hex address anywhere — an address comes from the owner or a read.
- No `TODO` / `FIXME`.
- No override phrasing: "ignore previous", "ignore all previous", "override", "SOUL.md",
  "do not tell", "disregard your instructions".
- No mentions of the retired runtime: `bevo-hub`, `AGENTS.md`, `openclaw`,
  `bevo-location`, `bevo-duty`, `web-checkout` (case-insensitive).

## Versions, publishing, yanking

- `CHANGELOG.md` has an entry — a heading such as `## 1.0.2` or `## [1.0.2] - <date>` —
  for the frontmatter `version`.
- **Every listed skill is validated on every publish build, and any error fails the whole
  build** (the last deploy stays live). A skill that stops validating blocks publishing
  until it is fixed or de-listed.
- **Requirements are checked across the build, too.** A published skill whose
  `requires.skills` names one the build does not publish — not listed, or its current
  version yanked — or a cycle of requirements fails the build. De-list or yank a required
  skill together with the skills that require it.
- **A `name@version` is immutable.** The build compares against the live index and refuses
  a version it already serves with different file hashes, or one it has yanked. Change a
  skill → bump `version` → add its `CHANGELOG.md` entry.
- Yanking `name@X.Y.Z` (in `yanked.json`) publishes a tombstone
  `{name, version, yanked: true, files: []}`; see [SECURITY.md](SECURITY.md).

## A minimal valid skill

````markdown
---
name: tip-once
description: Send one member a one-off tip in USDC when your owner asks, and say where it landed.
version: 1.0.0
metadata: {"butler":{"moneyMoving":true,"keywords":["tip","send a tip","thank you"],"requires":{"bins":["bevo-read","bevo-send"]}}}
---

## When to use

Your owner asks you to tip someone once: "tip @alice 5 USDC".

## Before you start

Resolve who they mean before any money moves:

```sh
bevo-read user @alice
```

## Procedure

1. [ADAPT] Say back the handle and the amount, in your owner's words.
2. [FIXED] Send it once:

   ```sh
   bevo-send --to @alice --amount 5 --token usdc
   ```

## Idempotency and retries

One tip per request. On an error or an unknown outcome, look the printed key up with
`bevo-read request <key> --route transfer` — do not re-run the send.

## Failure handling

| Outcome | What to do |
| --- | --- |
| No such member | Ask your owner who they meant. |
| Rejected | Say why, and stop. |

## Limits

One recipient, one tip. A tip every week is a duty, not this skill.

## Say to the owner

"Your 5 USDC tip to @alice is on its way."
````

with a `README.md` and a `CHANGELOG.md` holding `## 1.0.0` beside it.

## Local validation

`scripts/validate.py` is a single-file, stdlib-only tool, published at
`https://virtual-protocol.github.io/butler-skills/tools/validate.py`. A skill repo needs no
registry checkout:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
python3 validate.py --standalone .            # add --maintainer for a butler- name
```

In CI it is one step: `uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`
(with `maintainer: "true"` for a `butler-` name). Keep the downloaded `validate.py` out of
the commit — the validator warns when it sees it.
