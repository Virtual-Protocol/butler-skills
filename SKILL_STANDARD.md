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

**Published to butlers: `SKILL.md`, `references/**/*.md`, `scripts/**/*.py`,
`scripts/**/*.mjs` and a root `package.json` / `package-lock.json`, nothing else.** A
published path is letters, digits, `.`, `_` and `-` segments that do not start with `.`;
another file under `references/` or `scripts/` is a warning (not published), a `.md`,
`.py` or `.mjs` path outside that shape is an error. `node_modules` is never published
and never counted. `assets/` is warned about (not published). A script is for code a
skill would otherwise have the model retype: the butler writes it as a plain file and
`SKILL.md` runs it in place, as `python3 "$HOME/skills/<name>/scripts/<file>.py"` or
`node "$HOME/skills/<name>/scripts/<file>.mjs"`. Name every script in `SKILL.md` or a
reference: an install from a GitHub link fetches only the files the text names, and a
script named nowhere is a warning. Keep tests out of `scripts/` (they would be published).

**Node scripts.** The npm manifest pair sits at the skill **root**, never under
`scripts/`: the butler runs `npm ci --omit=dev --ignore-scripts --no-audit --no-fund` in
the skill directory, and Node resolves a script's imports upward. Pin exact versions and
keep the dependency closure small (a command times out after 120 s).
A `package.json` without `package-lock.json` (or the reverse) is an error, and an install
hook (`preinstall`, `install`, `postinstall`, `prepare`) is a warning: none ever runs under
`--ignore-scripts`. CI runs that same `npm ci` on every listed skill (`validate.py --all
--install-check`) and fails when `node_modules` exceeds 150 MB; if a dependency is heavy,
bundle the script instead. The same check runs `npm audit signatures` and fails on any invalid or
missing registry signature or provenance attestation. A script that signs
imports the container's signer (`await import(process.env.BEVO_SIGNER)`: `mainSigner`,
`agentKey`) and never holds a key; it declares `requires.bins` `["bevo-wallet", "node",
"npm"]` and `moneyMoving: true` (virtuals-agent `docs/skills-contract.md`). A local
signer for testing lives outside `scripts/` (for example `dev/`), so it is never published.

A `duty.py` is an
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
| Translations | optional: `i18n/descriptions.json` may give the entry's `description` in the app's other languages, `{"<name>": {"ko": "...", "zh": "..."}}` — each one line of at most 200 characters, the file sorted by name, every name listed. The build publishes it as the index row's `descriptionI18n` (display text for the app; the container ignores it). A listed entry with no translation for a language is a warning, not an error — the app shows the English there. The English stays in the entry's own repo, so when a new version rewords `description`, update its translations here |

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
| `name` | Mastra's rule: `^[a-z0-9]+(-[a-z0-9]+)*$` (lowercase, digits, single hyphens, none leading or trailing), at most 64 characters, and not something YAML reads as a number or date (`123`, `1e5`, `2026-09-21`). In registry mode it must equal the directory — the `skills.json` entry, and the directory the butler installs it in. Not in `schema/reserved-names.json` (which includes `acp-cli` and `duty-code`, the two skills compiled into the butler image). `bevo-` is **refused** — the container's bundled-command namespace |
| `description` | at most 200 characters, one line. Either a JSON double-quoted string (`description: "Read it: all of it"`) or a plain value YAML reads back verbatim: no `: `, no ` #`, not ending in `:`, not starting with any of `` - ? : , [ ] { } # & * ! \| > ' " % @ ` ``, and not a number, boolean, null or date. The validator's error spells out the quoted form to paste |
| `version` | semver `X.Y.Z`; bump it for every change — a published `name@version` never changes bytes |
| `metadata` | ONE line of JSON: `{"butler": {"moneyMoving": <bool>, "keywords": [<non-empty strings>], "requires": {"bins": [<commands>]}}}` — all three fields required; the only optional ones are `maxSteps`, `requires.skills`, `venue` and `portfolio` (below); no other keys anywhere, no duplicated JSON key |

The OpenClaw-era fields are refused by name, with where their content goes now:
`metadata.openclaw`, `metadata.butler.tier` / `modes` / `params` / `web3` / `dutyTemplate`,
and `metadata.butler.requires.routes` / `features` / `gates`. So are other Mastra keys
(`license`, `user-invocable`, …): the frontmatter is exactly the four above.

`requires.bins` lists every program the skill's shell blocks run (see Commands) — `curl`,
`python3`, `jq` as much as `bevo-read` — and no shell builtin (`cd`, `export`, …) or path.
The butler checks it has each one on PATH before installing.

### Optional: `maxSteps` and `requires.skills`

```yaml
metadata: {"butler":{"moneyMoving":true,"keywords":["grabfood","order food"],"maxSteps":150,"requires":{"bins":["app-checkout","bevo-read"],"skills":["butler-app-checkout"]}}}
```

| Field | Rule |
| --- | --- |
| `maxSteps` | an integer from 20 to 500: how many agent steps a turn that loads this skill may take. A butler turn gets 200 by default; loading the skill raises that turn's budget to `maxSteps`, capped by the container's own ceiling (500 unless it is configured otherwise). Set it for a long errand — it also covers containers still on an older, lower default — and leave it out when the default is enough |
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

### Optional: `venue`

A skill that holds money on an outside venue (Polymarket, Derive, Hyperliquid, outcome.xyz, …) declares how the Butler checks and empties it when a duty wallet is torn down. The block sits in the one-line `metadata` JSON, beside `moneyMoving`:

`"venue":{"id":"polymarket","precheck":["python3","scripts/venue.py","precheck"],"withdraw":["python3","scripts/venue.py","withdraw"]}`

| Field | Rule |
| --- | --- |
| `venue` | an object with exactly `id`, `precheck` and `withdraw`; `metadata.butler.moneyMoving` must be `true` |
| `id` | `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 32 characters |
| `precheck`, `withdraw` | each an argv array of 1 to 8 strings, each 1 to 200 characters with no control characters. `argv[0]` is listed in `requires.bins` and is not a shell builtin. An element shaped `scripts/<path>.py`, `.mjs` or `.js` is a published file of the skill (only `.py` and `.mjs` can be published). Neither array contains `--wallet-id`, `--address`, `--json`, `--all` or `--cash` (the Butler appends them), nor the retired `--wallet`. The two arrays differ |

The ids `hyperliquid-perp` and `hyperliquid-spot` are reserved: bevo-server owns them as server venues, and a skill may not declare them.

**How the Butler runs it:** no shell, 120 s timeout. stdout must be exactly one JSON object, at most 64 KiB.

- precheck: argv + `--wallet-id <walletId> --address <address> --json`
- withdraw: argv + `--wallet-id <walletId> --address <address> (--all|--cash) --json`

`--wallet-id` is the Butler wallet's id, the one bevo-server signs with; `--address` is its public address, for reads.

**precheck output:**

`{"venue","readable":bool,"reason":str|null,"freeUsd":num,"inTransitUsd":num,"positions":[{"key","title","valueUsd":num|null,"closable":bool}],"orders":[{"key","title","reservedUsd":num|null}]}`

Amounts are USD and at least 0. The venue is **unreadable** on any of: a non-zero exit, a timeout, bad JSON, `venue` not equal to `id`, `readable:false`, or `freeUsd` / `inTransitUsd` missing or negative. It is **empty** when it is readable, has no positions, no orders, and both amounts are under 0.01. Unreadable never counts as empty.

A venue that lives inside another venue's account (Outcome inside Hyperliquid, say) reports in `freeUsd` only the cash that is its own, and 0 for the shared account's cash, so a teardown never counts the same dollars twice.

**withdraw behaviour:** `--cash` cancels every open order and returns all free venue cash to the same sub-wallet on-chain. `--all` additionally market-closes every position first. It is idempotent and re-runnable, and it never sends to any address but the same sub-wallet.

**withdraw output:**

`{"venue","mode":"all"|"cash","moved":[{key,title,valueUsd,txHash|null}],"pending":[{key,title,valueUsd,approvalId|null}],"kept":[{key,title,valueUsd,"reason":"position_kept"|"not_closable"|"close_failed"|"below_minimum"|"refused"|"unreadable"}]}`

### Optional: `portfolio`

A skill for a venue where the owner holds positions (Outcome, Polymarket, Derive, …) declares a read-only script the Butler runs to sync what the owner holds there. It is a **separate** key from `venue` — a different script with a different contract — and it does **not** require `moneyMoving`. It sits in the one-line `metadata` JSON:

`"portfolio":{"script":"scripts/portfolio.mjs","site":"https://outcome.xyz","name":"Outcome","chains":["evm"]}`

| Field | Rule |
| --- | --- |
| `portfolio` | an object with exactly `script`, `site`, `name` and `chains` |
| `script` | a published `scripts/**/*.mjs` or `.py` file, named in SKILL.md. Read-only: it never signs, never moves money, never creates a credential |
| `site` | an https URL. Its host is the identity of the row the app shows, so it is unique across the registry (`validate.py --all` refuses a host two skills declare) |
| `name` | 1 to 40 characters: the brand name the app shows |
| `chains` | a non-empty subset of `evm` and `solana`, each at most once: which kinds of wallet the script can read |

The body must also contain a `### Portfolio` subsection (a subsection, so the seven `##` sections stay as they are). It must:
- name the script, and each exported read function and subcommand, with one example command and one example output;
- say what `valueUsd` counts, and what it leaves to the wallet's own figures;
- say what each position and order field means at this venue;
- list the error codes the script uses;
- tell chat how to answer "my positions on X": read the stored rows, or run the same script with `--address`.

**Command line.** The runtime is chosen by the file extension:

```
node|python3 "$BEVO_SKILLS_DIR/<skill>/<script>" [--wallet-id <walletId>] --address <address>
```

- `--address <address>` prints the full envelope below. The Butler also passes `--wallet-id <walletId>` for a Butler wallet (none for the owner's main wallet), so the script must accept it.
- The subcommands `positions`, `orders` and `balances`, each with `--address`, print one read each, so chat can answer a narrow question.
- The exit code is always 0. The script finishes within 30 s, prints exactly one JSON object to stdout, and never prints a secret.

**Envelope.** One JSON object `{ok, status, data, error}`:

| Output | Meaning |
| --- | --- |
| `{"ok":true,"status":"done","data":<Portfolio>}` | the venue was read; an empty account is `valueUsd: 0` |
| `{"ok":true,"status":"not_used"}` | the venue has no account for this wallet at all |
| `{"ok":false,"status":"error","data":null,"error":{"code","message","hint"?}}` | the read failed; `status` is `"error"` or absent, `message` is at most 200 characters, `hint` is an optional string |

Error codes a script emits: `RATE_LIMITED`, `VENUE_DOWN`, `NO_CREDENTIAL`, `CREDENTIAL_INVALID`, `BAD_ARGS`, `UNEXPECTED`. The Butler's sync adds `TIMEOUT`, `BAD_OUTPUT`, `DEPS_MISSING`, `SCRIPT_MODIFIED` and `SKILL_MISSING` itself; a script never emits those.

**Portfolio (`portfolio/v1`)** — exactly these keys, no others:

```
{ wallet, asOf (ISO-8601), currency: "USD", valueUsd (>= 0), availableUsd: number|null,
  positions: Position[] (at most 50), orders: Order[]|null (at most 50) }
Position = {id, market, outcome|null, side, size, unit, entryPrice|null, markPrice|null,
            valueUsd|null, pnlUsd|null, details?: [{label, value}] (at most 8)}
Order    = {id, market, outcome|null, side: "buy"|"sell", type: "limit"|"stop"|"tp"|"sl"|"other",
            price|null, size, unit, filled|null, reservedUsd|null, placedAt (ISO-8601)|null}
```

Field caps, the same as the server's schema (strings are trimmed, then 1 character minimum):

| Field | Cap |
| --- | --- |
| `wallet`, `id`, `market`, `details[].value` | string, at most 120 |
| `outcome` | string, at most 80, or null |
| `side` (position) | string, at most 20 |
| `unit` | string, at most 20 |
| `details[].label` | string, at most 40 |
| `valueUsd` (portfolio) | number, 0 to 1e15 |
| `availableUsd`, position `valueUsd` / `pnlUsd` | number, -1e15 to 1e15 (null where the shape allows) |
| position `size` | number, -1e15 to 1e15 |
| `entryPrice`, `markPrice`, order `price` | number, 0 to 1e15 (null where the shape allows) |
| order `size`, `filled`, `reservedUsd` | number, 0 to 1e15 (null where the shape allows) |
| `asOf`, `placedAt` | ISO-8601 with seconds and a `Z` or `+HH:MM` offset |

`orders: null` means "not read", never "none". Stablecoins count as 1 USD.

**A venue built on another venue reports only its own holdings.** What the base layer already reads for the wallet (cash, perps, orders) is not repeated. Outcome.xyz runs on Hyperliquid, whose balance, perps and orders the owner's wallet read already covers, so its portfolio reports **only the bets held**: one position per outcome held, `availableUsd: null`, `orders: null`, and `valueUsd` the sum of the bet values at mark. Counting the base layer's cash or orders here would sum it twice.

**Ship the read code with tests.** Put the reads in `scripts/lib/portfolio.mjs` (or the language's equivalent): one exported function per read (`getPositions`, `getOpenOrders`, `getBalances`) and a pure `toPortfolio(...)` that maps them into `portfolio/v1`, with the network client injectable. A skill that declares `portfolio` must ship a `test/` (or `tests/`) directory with tests that cover `toPortfolio` with fixtures, with no network: the mapping, an empty account, no account (`not_used`), and a venue 429 or 5xx (`RATE_LIMITED` / `VENUE_DOWN`). `validate.py` checks that the directory holds a file; running the tests is the skill repo's own job.

**Check a captured output:** `python3 validate.py --portfolio-output out.json` validates one captured stdout against the envelope and `portfolio/v1` above (status, field types, the caps, `currency` `"USD"`, `valueUsd` at least 0) and exits non-zero with the problems listed.

The `portfolio` declaration reaches the index row as `portfolio: {script, site, name, chains}`, absent when the skill declares none.

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
- A **money command** — `acp trade`, `acp wallet send-transaction`,
  `acp wallet sign-typed-data`, `acp wallet sign-message`, `acp card`,
  `acp options open|buy|close|deposit|withdraw`,
  `bevo-send`, `bevo-wallet fund|withdraw|bridge-prl|bridge-wprl|bind`, `app-checkout checkpoint` — may only appear in a shell block inside a
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
`||`, `;` and `&`, so **every** command on it is checked, not just the first. Commands
inside `$(…)`, backticks and `<(…)` are checked too, and a `VAR=value` prefix or an
`if` / `do` / `(` in front of a command is read past.

**A skill may run any program** — `curl`, `wget`, `python3`, `node`, `jq`, anything on the
container's PATH — as long as `requires.bins` declares it. The container's own commands
are held to what the container accepts, so a skill cannot call one it lacks:

| Command | Allowed subcommands (argv[0], or the acp group) |
| --- | --- |
| `bevo-read` | `get`, `messages`, `channel-messages`, `participants`, `summary`, `search`, `groups`, `user`, `assets`, `me`, `policy`, `token-search`, `token`, `token-balance`, `stock-min`, `token-stats`, `trade-activity`, `trade-executions`, `wallet-transfers`, `request`, `card-budget` |
| `bevo-sms` | `number`, `status`, `otp` |
| `bevo-wallet` | `chains`, `list`, `create`, `unbind`, `archive`; `fund`, `withdraw`, `bridge-prl`, `bridge-wprl` and `bind` move money |
| `bevo-automation` | `create`, `validate`, `update`, `enable`, `disable`, `delete`, `list`, `show`, `logs` |
| `bevo-x` | `search` |
| `app-checkout` | `start`, `status`, `screen`, `shot`, `tap`, `type`, `key`, `swipe`, `wait`, `install`, `open`, `grant`, `do`, `checkpoint`, `end` |
| `acp` | the groups the container's `acp` wrapper lets through: `agent` (only `whoami`, `list`, `use`, `link`, `generate-signer-key`, `signer-status`, `help`), `browse`, `card`, `chain`, `email`, `events`, `job`, `message`, `offering`, `options` (`account` reads; `open`, `buy`, `close`, `deposit`, `withdraw` move money), `policy`, `provider`, `resource`, `site` (`acp site list`, `acp site <name> <action>`, `acp site status`: bevo-server's site rail; a site's money action files a card), `skill`, `subscription`, `trade`, `wallet` (`send-transaction`, `sign-typed-data` and `sign-message` move money: a signature can authorize a transfer). `client`, `compute` and `configure` are refused; a bare `acp` / `acp --help` is refused |
| `bevo-send`, `bevo-rpc`, `bevo-notify` | any arguments |

Any other `bevo-*` name is refused: it is not the container's, and the butler's hub
reports it missing. Write alternatives in prose, never as `a|b` inside a command: that is
a pipe, and its second half is a command that must be declared.

The lint sees shell lines, not what a program does: a money command run from inside a
`python3` script or a heredoc is invisible to the `[FIXED]`-step rule above. That, and
what a skill fetches or sends, is the maintainer's review of every skill PR.

The table is read off virtuals-agent: `BUTLER_COMMANDS`
(`src/integrations/butler/launchers.ts`), each command's own dispatch in
`src/integrations/butler/bin/`, and the acp wrapper (`src/integrations/acp/wrapper.ts`).
A command the container gains is a change here first.

## Lints (every published file)

- No secrets: `brt_…`, `sk-…`, 64-hex strings, JWTs.
- URLs are not linted in a skill: name any data source or page the skill needs. Duty
  templates stay GitHub-only. Review still holds a phone-app skill to the official app
  stores (Huawei AppGallery, Google Play) — never an APK mirror, since the owner's card may
  be typed into that app.
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
python3 validate.py --standalone .
```

In CI it is one step: `uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`.
Keep the downloaded `validate.py` out of
the commit — the validator warns when it sees it.
