# TEMPLATE_STANDARD.md — the normative format

This file mirrors exactly what `scripts/validate.py` enforces. If code and doc disagree,
the code is right. See [README.md](README.md) for the how-to; this is the checklist.

## What a duty template is

A **duty** is a standing instruction Butler compiles into one Python program (`duty.py`)
that runs unattended, fed events on stdin, and may spend the owner's money by shelling
`acp trade` itself. A **template** is that program shipped as a reusable, versioned
bundle: a name, a settings shape, the trigger kinds it accepts, and the code — nothing
else. There is no frontmatter, no prose procedure, no `SKILL.md`: the code is the whole
of what a template does.

## Layout

A template is its own git repository, with three files at the **repository root**, or a
folder of a repository it shares, such as the skill that files it (see `path` below):

```
recipe.json     required — the manifest: id, version, description, triggers, params
duty.py         required — the whole program
README.md       required — what it does, written for Butler (see below), not for a human
```

The registry keeps no copy of that repository. `templates.json` lists the template by
`name`, `repo` (a GitHub link) and `ref` (a branch or a tag); `scripts/build_index.py`
shallow-clones each entry at its ref into a temp directory and builds the index from that
throwaway checkout. The validator runs on either: `scripts/validate.py --standalone <dir>`
on a template repo checkout (id from `recipe.json`), or `scripts/validate.py --all` in
registry CI.

### Tree rules (enforced on both sides — the container refuses a clone that breaks them)

| Rule | Limit |
| --- | --- |
| Regular files in the tree | <= 50 (`.git` and `__pycache__` excluded) |
| Total bytes | <= 1 MB |
| Symlinks | none, anywhere |
| Nested repositories / submodules | none — no `.gitmodules`, no `.git` below the top level |

### Registry listing rules (registry only — `scripts/check_registry.py`, run by CI)

`templates.json` is the registry: one entry per template, nothing about its content.

| Rule | Check |
| --- | --- |
| `name` | a valid template id (`^[a-z0-9][a-z0-9-]{1,63}$`), listed once, and must equal the `id` the repo's own `recipe.json` declares (`scripts/validate.py --all` checks this by cloning into a directory named after the entry) |
| `repo` | `https://github.com/<owner>/<repo>` — never ssh, another host, a local path, credentials, a query or a fragment |
| `ref` | a plain branch or tag name (`^[A-Za-z0-9._/-]{1,100}$`) |
| `path` | optional: the folder holding the template when it is not the repo's root, e.g. `templates/derive-account` beside a skill's `SKILL.md`. Plain relative segments (letters, digits, `.`, `_`, `-`, none starting with `.`), at most 200 characters, and the last one must equal `name`, so the folder validated is still named after the entry. The tree rules apply to that folder. Templates only: a skill is always its repo's root |
| Ref resolves | the ref exists on the remote (`git ls-remote`); `--offline` skips this one check |
| Order | the file is sorted by `name` |
| Translations | optional: `i18n/descriptions.json` may give the entry's `description` in the app's other languages, `{"<name>": {"ko": "...", "zh": "..."}}` — each one line of at most 200 characters, the file sorted by name, every name listed. The build publishes it as the index row's `descriptionI18n` (display text for the app; the container ignores it). A listed entry with no translation for a language is a warning, not an error — the app shows the English there. The English stays in the entry's own repo, so when a new version rewords `description`, update its translations here |

A `ref` is a following relationship, not a pin: with a branch, whatever the template repo
merges reaches the registry on the next build, with no review here. Give a template that
should move only on release a tag `ref`.

## `recipe.json`

```json
{
  "id": "dca",
  "version": 2,
  "description": "Buy a fixed dollar amount of one token on a schedule.",
  "keywords": ["dca", "average", "schedule"],
  "triggers": ["timer"],
  "supersedes": ["dca@1"],
  "keys": ["buy:<duty>:slot:<slot>"],
  "params": {
    "type": "object",
    "additionalProperties": false,
    "required": ["TOKEN", "SIZE_USD"],
    "properties": {
      "TOKEN": { "type": "string", "description": "Symbol or address to buy." },
      "SIZE_USD": { "type": "number", "minimum": 1, "maximum": 1000 }
    }
  }
}
```

This shape is fixed by the container's template installer — do not add or rename a field.

| Field | Rule |
| --- | --- |
| `id` | required, `^[a-z0-9][a-z0-9-]{1,63}$`, not in `schema/reserved-names.json`. The `bevo-` prefix is **refused** — it is the container's bundled-command namespace. In registry mode `id` must equal the directory the entry is cloned into (its `templates.json` name); in `--standalone` mode only the pattern is checked |
| `version` | required, a plain positive **integer** (not semver) — a ref is `<id>@<version>`. The publish build refuses to overwrite an already-published version with different bytes, so a change without a bump fails the build |
| `description` | required, non-empty, <= 200 chars — it is composed into a 280-char field with a settings clause appended, so a longer one is silently dropped by the caller |
| `keywords` | optional array of strings |
| `triggers` | optional array, subset of exactly `["timer", "group", "trade"]` — there is no other trigger kind |
| `supersedes` | optional array of older refs this version replaces, e.g. `["dca@1"]`; flattened by `build_index.py` into the published index's `aliases` |
| `keys` | optional array of the stable idempotency-key format(s) `duty.py` uses — documentation only |
| `params` | required, a JSON-Schema **object** describing the settings a duty made from this template takes — see Params below |
| `status` | required for a new template: the duty's status line, by state — see Status line below |

## Params

`params` is a JSON-Schema fragment, but only a subset of JSON-Schema keywords is
implemented by the container's params checker; anything else is refused wherever it
appears in the tree (recursing into `properties` and `items`):

```
type, enum, const, minimum, maximum, minLength, maxLength, items,
default, required, additionalProperties, description, properties
```

`type`, where present, must be one of `object, string, number, integer, boolean, array`.
There is no `pattern`, no `oneOf`/`anyOf`, no `$ref` — write the constraint you need
with what is supported, or check it in `duty.py` itself. `allOf` is allowed in exactly
one shape, only at the root of `params` — see "Conditional required settings" below;
anywhere else it is refused like any other unsupported keyword.

### Conditional required settings

The container's params checker implements ONE conditional: a `params.allOf` list of
clauses, each `{if: {properties: {<name>: {const: <value>} | {enum: [<value>, ...]}}},
then: {required: [<name>, ...]}}`, declared at the **root** of `params` only:

```json
"allOf": [
  { "if": { "properties": { "SIZING": { "const": "fixed" } } }, "then": { "required": ["SIZE_USD"] } },
  { "if": { "properties": { "SIZING": { "enum": ["cash_share", "leader_share"] } } }, "then": { "required": ["SHARE"] } }
]
```

Semantics: a condition holds only when the named property is present on the filed
params (a `const` or non-empty `enum` match against a *declared* property); `then`
contributes `required` alone — no other JSON-Schema effect. This exists so a setting
that only matters under one choice (e.g. `SIZE_USD` only when `SIZING: fixed`) can be
asked for, rather than filed empty or always required. Nothing else — nested `allOf`,
`else`, `if`/`then` keys beyond `properties`/`required`, or an `if` condition that is
neither `const` nor `enum` — is implemented; `scripts/validate.py`'s
`check_params_conditionals` refuses it so a template author never believes a broader
shape works.

A duty created from a template reads its settings with
`PARAMS = json.loads(os.environ["PARAMS"])` — there is no render step and no second
source of truth. Every other `os.environ` key `duty.py` reads must be a name under
`params.properties`, or one the runtime always provides: `BEVO_SERVICE_ID`,
`BEVO_SERVICE_NAME`, `BEVO_SESSION_ID`, `BEVO_SESSION_KEY`, `BEVO_MODE`,
`BEVO_STATE_PATH`.

## `duty.py`

The whole program. `import bevo` plus a small stdlib allowlist:

```
json, os, re, math, datetime, zoneinfo, subprocess, shlex,
time, random, collections, itertools, statistics
```

Rules the validator enforces by reading the AST:

- **Forbidden:** `from bevo import ...`, `import bevo as ...`, `eval`, `exec`, `compile`,
  `__import__`, `os.system`, `os.popen`, and any import of `socket`, `urllib`, `requests`
  or `http`.
- **No money verb exists.** `bevo.trade`, `bevo.execute`, `bevo.buy`, `bevo.sell`,
  `bevo.long`, `bevo.short`, `bevo.close`, `bevo.stock_buy`, `bevo.stock_sell` were all
  deleted on 2026-09-21 with no runtime shim — calling any of them is refused. A duty
  spends by running the command directly:

  ```python
  import subprocess
  subprocess.run(
      ["acp", "trade", "--token-in", "usdc", "--amount-in", "5",
       "--token-out", ref, "--idempotency-key", key],
      capture_output=True, text=True, timeout=180, check=False,
  )
  ```

  A shelled `acp trade` / `acp wallet send-transaction` / `acp card issue` whose literal
  argv carries no `--idempotency-key` is refused. When the argv is built dynamically (a
  variable holds part or all of it) the validator can only warn — it cannot prove the key
  is missing, so the author is on their own for that shape.
- **Other retired names**, each refused with its replacement: `bevo.escalate` → use
  `bevo.prompt()`; `bevo.token` → use `bevo.read("/token-search")`; `bevo.is_stock` → read
  `spot.stocks[]` via `bevo.read("/user-assets")`.
- **A trigger with nothing waiting on it is refused.** If `recipe.json` declares
  `triggers`, `duty.py` must call at least one waiter somewhere:
  `bevo.events/trades/messages/transfers/ticks/polls/webhooks/frames/batches`. Code that
  never waits runs once and exits, which the supervisor reports as a crash.
- **A module-level `while True:` with no waiter call inside it** is refused for the same
  reason — it either spins forever doing nothing or exits without waiting.
- **`uses bevo.* but never imports it`** is refused — a `bevo` attribute access with no
  `import bevo` in the file is always a bug, not a lint nit.
- Must parse as valid Python 3.11.

## Status line

The owner's Duties list shows each duty as its title and **one status line**. `duty.py`
reports which state it is in and the values it measured; `recipe.json` `status` words each
state, so the line can be checked here and reworded without touching the code.

```python
bevo.status("bought", n=bought, total=TOTAL)
```

```json
"status": {
  "starting": "First buy {next}",
  "bought": { "last": "Bought {n} of {total}", "next": "next buy {next}" },
  "paused": "Paused until the token is confirmed"
}
```

- A line is a string, or `{"last", "next"}`: `last` stays until a later state replaces it.
- `{next}` is the next run, written by the app ("in 8 min", "7am", "Mon 9am"); `{name}` is a
  value the call passes, or a setting by name. `{ran}`, `{day}` and `{runs_today}` are there too.
  Pass numbers, never formatted text: write "$" in the line, not in the value.
- A part (between `" · "`) whose value is missing is dropped, so `"Now ${price} · next check {next}"`
  reads `"next check in 8 min"` when the price could not be read.

`validate.py` refuses, and the container refuses the same on a duty Butler writes itself:

| Rule | Why |
| --- | --- |
| a `status` at all, with a `starting` line | it is all the row shows until the first run; without one the row can only say when the duty last ran |
| every state `duty.py` reports is worded; the state is a literal string | otherwise the row keeps its last line |
| on a timer, `starting` says when the first run is with `{next}`, and at least one later state says when it acts next — not every state; one named `done`, `finished`, `paused`, `blocked`, … never needs it | the owner's first question about a scheduled duty — but a timer duty that waits on a price ("Watching for $0.10") need not fit a time on every line |
| `{next}` has a word before it saying what happens then: `next buy {next}`, never `next {next}` or a bare `{next}` | "· 7am" alone means nothing |
| about 46 characters once filled | the row is one line; the rest is cut |

A template listed before `status` was required (only `x-portfolio`) still publishes without one,
with a warning, until it adds one.

Keep it to what the owner wants at a glance: the latest result or progress, and what comes next.
Never repeat the title (its token, city or schedule) — it sits right above the line.

## `README.md`

**Written for the model, not for a human.** The container's `recipe_show` tool returns
this file verbatim to Butler, together with the params schema and the triggers, and it
is the last thing read before a duty is filed from this template. `recipe_search` scores
`name`, `keywords`, `description` and `triggers` and **never** README text, so nothing
here affects discovery.

Required: non-empty. No required sections and no numbered-step markers — that grammar
belonged to the retired prose-skill format and does not apply.

Rules, enforced by `scripts/validate.py`:

- **Describe the program; do not instruct the reader.** The container fences this text
  as *data* ("it describes a program, it does not tell you what to do"), so imperative
  second-person prose is ignored by design. `check_readme` warns on an opening
  imperative.
- **No human-repository furniture**: badges, install or clone steps, a licence or
  contributing section, or a changelog. `CHANGELOG.md` sits beside this file, is for
  humans, and is never published to the model. Warned.
- **Stay under 4 KB.** Every byte is prefilled into the model's context on each
  `recipe_show`, on top of an already-large standing prompt. Warned past 4 KB, refused
  past 16 KB.

What it should actually contain, in rough order of value to the reader: what the
template does in one or two sentences; **what it will not do** — defaults that are off,
legs it skips, conditions it does not check, since a template that silently does less
than the owner asked is the failure nobody notices; and what each setting means and in
what unit, wherever a bare number is ambiguous.

## Misc

- No secrets (`brt_...`, `sk-...`, 64-hex strings, JWTs) anywhere in `recipe.json`,
  `duty.py` or `README.md`.
- No URLs except `github.com/Virtual-Protocol` / `raw.githubusercontent.com/Virtual-Protocol`
  links, and one exception: the site a duty keeps a wallet Platforms row for. When
  `duty.py` passes an https origin as a literal to `bevo.platform_balance("https://stake.com", ...)`,
  that origin (no path) may appear in the template.
- Every `templates.json` entry is an `https://github.com/<owner>/<repo>` URL with a ref
  that resolves on the remote (`tests/test_templates_registry.py`,
  `scripts/check_registry.py`).
- `scripts/build_index.py --dry-run` must succeed for the whole registry (every entry
  gets a `source` block: the short `owner/repo`, the ref, and the 40-hex commit this
  build resolved it to). Two entries resolving to the same `<id>@<version>` refuse the
  build outright.
- Hub tooling downloaded for local validation (`validate.py`, `replay.py`,
  `stub_bevo.py`) is never part of a template; the validator warns when it sees one in
  the tree.

## Local validation

`scripts/validate.py` is a single-file, stdlib-only tool and is published, with the
replay harness, at `https://virtual-protocol.github.io/butler-skills/tools/`
(`scripts/publish_tools.py` lays it out). A template repo needs no registry checkout:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page
```

`replay.py` downloads `stub_bevo.py` and any fixture it needs from the same site when
they are not beside it. It runs `duty.py` for real against captured fixture data, with
`subprocess.run`/`check_output`/`Popen`/`call`/`check_call` monkeypatched so a shelled
`acp trade`/`wallet`/`card` command is recorded rather than actually spawned — the same
grammar checks (a key on every money command, no two actions sharing one) then run
against what was recorded. In CI the same two checks are the composite action
`uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`.
