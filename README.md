# butler-skills

The **remote duty-template registry** for Butler (the `virtuals-agent` container). It is
a link directory, not a store of content: [`templates.json`](templates.json) lists each
template by `name`, a GitHub `repo`, and a `ref`; every build clones each entry at its
`ref`, validates it, and republishes **one** `index.json` (plus the template files
themselves) to GitHub Pages. A PR here adds or removes a row — it never edits a
template's content, which lives entirely in that template's own repository.

## What a duty template is

A **duty** is a standing instruction the owner gave Butler in one sentence — "buy $5 of
VIRTUAL every morning", "copy wallet 0xf38e… on Base" — compiled into **one Python
program** (`duty.py`) that runs unattended, fed events on stdin, and may spend the
owner's money. There is no separate "judgment stage" and no rendering step: the code the
container runs is the code the template ships, verbatim.

A **template** is that program, packaged as a reusable, versioned bundle at the root of
its own git repository:

```
recipe.json     the manifest — id, version, description, triggers, params
duty.py         the whole program
README.md       prose, for a human reading the registry
```

See [TEMPLATE_STANDARD.md](TEMPLATE_STANDARD.md) for the exact, enforced shape of each
file, and [CONTRIBUTING.md](CONTRIBUTING.md) for the PR process. This file is the
how-to; that file is the checklist.

## The three trigger kinds, and nothing else

`recipe.json`'s `triggers` array is a subset of exactly these three — there is no other
kind, and in particular **no market-data trigger** (no price, RSI, or indicator kind
exists; "buy X when ETH RSI < 40" is a `timer` whose code fetches the figure itself):

| kind | what fires it |
| --- | --- |
| `timer` | a schedule — once a day at a time, or every N seconds |
| `group` | messages in one of the owner's chat groups matching keywords/cashtags/senders |
| `trade` | trades on the public feed matching a principal, wallet, token or direction |

`duty.py` waits on whichever of these it needs with a typed generator:
`bevo.ticks()` / `bevo.messages()` / `bevo.trades()` (plus `bevo.transfers()`,
`bevo.polls()`, `bevo.webhooks()`, `bevo.frames()` and the raw `bevo.events()` /
`bevo.batches()`, for triggers this registry does not currently accept). A template that
declares a trigger but never calls the matching waiter is refused by the validator — that
shape runs once and exits, which the container's supervisor reports as a crash, not a
duty.

## How settings reach the code

`recipe.json`'s `params` is a JSON-Schema object naming the settings a duty made from
this template takes (only a small, container-checked subset of JSON-Schema is
supported — see TEMPLATE_STANDARD.md). Filing a duty from a template is one call:

```json
duty_create { "recipe": "dca@2", "params": { "TOKEN": "VIRTUAL", "SIZE_USD": 5 } }
```

The container fills any declared default, refuses an unknown key, and stores the row with
`code` = the template's `duty.py` verbatim and `env = {RECIPE: "dca@2", PARAMS: "{...}"}`.
`duty.py` reads its own settings with:

```python
import json, os
PARAMS = json.loads(os.environ["PARAMS"])
```

There is no render step and no second source of truth: changing a setting later is
`duty_update {params}`, which patches `env` only and restarts the child; editing the
*behaviour* is `duty_update {code}`, which strips `RECIPE` and keeps `PARAMS` — that is a
fork, and from then on the duty is no longer tied to this registry at all.

## How a duty spends

**There is no money verb.** Since 2026-09-21 a duty spends by shelling the exact command
the rest of the product already speaks:

```python
import subprocess
subprocess.run(
    ["acp", "trade", "--token-in", "usdc", "--amount-in", "5",
     "--token-out", ref, "--idempotency-key", key],
    capture_output=True, text=True, timeout=180, check=False,
)
```

Every money command must carry a literal `--idempotency-key`, derived from the event
being handled — never from a timestamp — so a redelivered event produces the same key and
bevo-server's ledger recognises the replay instead of spending twice. The validator
refuses a shelled `acp trade`/`wallet`/`card` command whose literal argv carries no key
(and warns, rather than refuses, when the argv is built dynamically and it cannot prove
the key is there or missing). `bevo.trade()`, `bevo.execute()` and every other
per-verb shortcut (`buy`/`sell`/`long`/`short`/`close`/`stock_buy`/`stock_sell`) were
deleted with no runtime shim — a template that calls one is refused outright, with the
replacement named in the message.

## Reads, notes, and asking the model

`duty.py`'s other tools:

- `bevo.read(path, params=None)` — any `/butler-read` endpoint, parsed JSON. Also
  `bevo.balance()`, `bevo.holdings()`, `bevo.stocks()`, `bevo.positions()`,
  `bevo.user(handle)`, `bevo.groups()`, `bevo.group_messages(id)` — thin wrappers over the
  same reads.
- `bevo.rpc(chain_id, method, params=None)` — a read-only JSON-RPC call.
- `bevo.notify(text, quiet=False)` — the ONLY way a duty reaches its owner. It tells; it
  can never ask, because a duty cannot hear a reply.
- `bevo.prompt(text, *, schema=None)` / `bevo.decide(question, options)` — ask the model
  for meaning (never for fetching or computing). Both **raise `BevoError` on every
  failure, including every replay** — there is no neutral answer to fall through on.
- `bevo.state` — a dict that persists across restarts. `bevo.allow(key, per_day=,
  per_hour=, max_usd=, usd=)` — a rate limit the duty imposes on itself, on top of
  `bevo.state`.
- `bevo.log(message)` — the duty's own log. Reaches nobody on its own; use `notify()` to
  tell the owner something.
- `bevo.exec_status(key)` — did a keyed action run? The one way to resolve an
  unparseable answer; never re-run the command to find out.

## Building a template

1. Create a repository (any host; GitHub is what this registry links to) with
   `recipe.json`, `duty.py` and `README.md` at its root. `scripts/new_skill.py <id>`
   prints the exact commands, including the one-line registry PR at the end.
2. Design the settings first: what does `params` need to say, and what does each
   default to? A template with no required params (everything has a sane default) is the
   easiest to recommend.
3. Write `duty.py`. Wait on the trigger(s) you declared, size and key every money
   command from the event you are handling, and `bevo.notify()` (not `bevo.log()`) for
   anything the owner should actually see.
4. Validate and replay locally — no registry checkout, no container, no Butler account:

   ```bash
   curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
   curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
   python3 validate.py --standalone .
   python3 replay.py --standalone . --fixture trade-activity-page
   ```

   `replay.py` runs your real, unmodified `duty.py` against captured fixture data with
   `bevo` swapped for `tests/stub_bevo.py` and `subprocess.run`/`check_output`/`Popen`/
   `call`/`check_call` monkeypatched so a shelled `acp trade`/`wallet`/`card` command is
   **recorded, never actually spawned** — then checks that every recorded money action
   carries a key and no two share one. It prints the recorded actions as JSON. A template
   whose CI should run the same two checks in one step uses the composite action:
   `uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`.
5. Open a PR here adding one row to `templates.json` (kept sorted by name):

   ```json
   { "name": "<id>", "repo": "https://github.com/<you>/butler-skill-<id>", "ref": "main" }
   ```

   CI checks the listing and that your `recipe.json`'s `id` matches this `name`; a
   maintainer review (two, if the template moves money) merges it. From then on, a new
   version is a release in **your** repo — bump `version` in `recipe.json`, merge (or
   tag, if you list at a tag `ref`) — never another PR here, unless the template is
   removed (`scripts/remove_skill.py`).

## Superseding an older version

`recipe.json`'s optional `supersedes` array names older refs this version replaces, e.g.
`["dca@1"]`. `scripts/build_index.py` flattens every template's `supersedes` into the
published index's top-level `aliases`, so a duty whose `env.RECIPE` still names `dca@1`
can be resolved forward without the container guessing.

## The published index

```json
{
  "schemaVersion": 3,
  "generatedAt": "2026-09-21T00:00:00Z",
  "templates": [
    {
      "name": "dca", "version": 2,
      "description": "Buy a fixed dollar amount of one token on a schedule.",
      "keywords": ["dca", "average", "schedule"], "triggers": ["timer"],
      "keys": ["buy:<duty>:slot:<slot>"],
      "source": { "repo": "Virtual-Protocol/butler-skill-dca", "ref": "main", "commit": "<40-hex>" },
      "files": [
        { "path": "recipe.json", "sha256": "<hex>", "bytes": 1198 },
        { "path": "duty.py", "sha256": "<hex>", "bytes": 5537 },
        { "path": "README.md", "sha256": "<hex>", "bytes": 1337 }
      ]
    }
  ],
  "aliases": [ { "ref": "dca@1", "supersededBy": "dca@2" } ]
}
```

served at `https://virtual-protocol.github.io/butler-skills/index.json`, with the
template files themselves under `templates/<name>/<version>/{recipe.json,duty.py,
README.md}` at the same base. `source.commit` — not `source.ref` — is what pins the
bytes: with a branch `ref` the commit moves whenever the template repo merges, and the
next build republishes it. Publishing is immutable per `name@version`:
`build_index.py` refuses to overwrite an already-published version whose bytes differ,
so a change without a version bump fails the build.

## Local testing, no infrastructure

Everything above runs from a checkout with `python3 -m pytest tests -q`:

- `tests/test_validate.py` — the checks in TEMPLATE_STANDARD.md, against the fixture
  templates in `tests/fixtures/templates/`.
- `tests/test_build_index.py` — recipe.json parsing, per-file hashing, the `source`
  block, `supersedes` → `aliases`, and the yanked tombstones, against synthetic git repos
  in `tmp_path`.
- `tests/test_replay.py`, `tests/test_stub_rails.py` — the offline replay harness and its
  `bevo` stand-in, including the subprocess money interception.
- `tests/test_standalone_tools.py` — the developer path that downloads only
  `validate.py`/`replay.py` and never clones this registry.
- `tests/test_check_registry.py`, `tests/test_templates_registry.py` — the
  `templates.json` listing itself.

No template repo is checked out in this repository, so none of the above touches the
network; a handful of `--all`/registry-mode tests build a real, throwaway git repo in
`tmp_path` to stand in for a clone.

## Reference

| File | What |
| --- | --- |
| `templates.json` | the registry |
| `yanked.json` | tombstoned `id@version` specs — see [SECURITY.md](SECURITY.md) |
| `schema/recipe.schema.json` | the `recipe.json` contract |
| `schema/index.schema.json` | the published `index.json` contract (schemaVersion 3) |
| `schema/reserved-names.json` | ids a template may never use |
| `scripts/validate.py` | the validator (also published standalone, stdlib-only) |
| `scripts/build_index.py` | clones every entry, validates nothing itself, writes `dist/` |
| `scripts/check_registry.py` | checks the `templates.json` listing itself |
| `scripts/new_skill.py` / `scripts/remove_skill.py` | print the exact commands to register / de-list or yank a template |
| `scripts/publish_tools.py` | lays out `dist/tools/` (the standalone `validate.py`/`replay.py`/`stub_bevo.py` + fixtures) |
| `tests/replay.py` | the offline replay harness |
| `tests/stub_bevo.py` | the `bevo` stand-in `replay.py` loads |
| `.github/actions/validate` | the composite action a template repo's own CI runs |
