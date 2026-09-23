# Contributing

This repo is the **registry** of the skills and duty templates the Butler container
installs. Every skill and every template is its own git repository; the registry is a
directory of links — `skills.json` and `templates.json` list each one by `name`, `repo` and
`ref`, and every build clones each entry at its `ref` and publishes an index of the
commits those refs resolved to. If you are a developer's Claude session with only this
repo's README URL, start there: [README.md](README.md). This file is the condensed
process/trust rules; README.md is the how-to.

## The standard

Everything CI enforces is written out in [SKILL_STANDARD.md](SKILL_STANDARD.md) (skills)
and [TEMPLATE_STANDARD.md](TEMPLATE_STANDARD.md) (duty templates) — they mirror exactly
what `scripts/validate.py` (and, for the registry listings, `scripts/check_registry.py`)
checks. If a doc and the validator ever disagree, the validator is right and the doc has
drifted (please file a PR fixing the doc).

## Where things live

| What | Where |
| --- | --- |
| A template's files (`recipe.json`, `duty.py`, `README.md`) | the template's own repo, at its root |
| A skill's files (`SKILL.md`, `README.md`, `CHANGELOG.md`, `references/`) | the skill's own repo, at its root |
| Team templates and skills | `Virtual-Protocol/butler-skill-<name>` |
| Community templates | the author's own GitHub repo |
| The registry entry | this repo: one `{"name", "repo", "ref"}` row in `templates.json` or `skills.json` — the whole of what the registry stores about it |
| The published index | GitHub Pages, built by `scripts/build_index.py`, which clones each entry at its `ref` into a temp directory and indexes that throwaway checkout |
| The standalone tools (`validate.py`, `replay.py`, `stub_bevo.py`, fixtures) | GitHub Pages `tools/`, laid out by `scripts/publish_tools.py`; a template repo's own CI is the composite action `.github/actions/validate` |

No template or skill content lives in this repo, so there is nothing here to edit for a
change to one.

## Listing a skill

A skill reaches every butler that asks for it, and it tells the model what to run — so the
skill listing is held tighter than the template listing:

- **Listing a skill is maintainer-only.** It is one PR adding a row to `skills.json`,
  opened by a `@Virtual-Protocol/butler-maintainers` member (CODEOWNERS covers the file).
  A community author who wants a skill listed asks a maintainer to review and list it.
- **Two maintainer reviews when the skill moves money** (`"moneyMoving": true`), one
  otherwise. The reviewers read the skill repo **at the `ref` being listed**.
- The skill must pass `python3 scripts/validate.py --all --maintainer` — the same check
  every publish build runs. **A listed skill that stops validating fails the whole publish
  build** (the last deploy stays live) until it is fixed or de-listed, so whoever lists a
  skill owns keeping its `ref` valid.
- **A skill that builds on others is listed with them.** Every skill named in its
  `requires.skills` must be listed too — in an earlier PR or the same one — or
  `validate.py --all` and the publish build refuse it; they also refuse a cycle of
  requirements. The same check stops the other direction: while a listed skill still
  requires one, de-listing that one (or yanking its current version) fails the build, so
  de-list the dependents in the same PR. On a butler, the hub installs required skills
  first, refuses to remove a skill another installed skill requires, and takes a de-listed
  required skill's dependents with it.
- **`maxSteps` is part of the review.** A skill that sets it (20–500) lets a turn that loads
  it run up to that many agent steps instead of the default 20, capped by the container's
  ceiling (200 unless configured otherwise) — read it like `moneyMoving`, as something the
  reviewers approve.
- A new version needs no PR here: bump `version` in `SKILL.md`, add its `CHANGELOG.md`
  entry, merge in the skill's repo. A published `name@version` never changes bytes — the
  build refuses a version the live index already serves with different content.
- `scripts/new_skill.py <name> --skill` prints the steps; `scripts/remove_skill.py <name>`
  de-lists or yanks one.

## Publishing an update to a template already in the registry

**Merge it in your own template repo. That is the whole procedure.** Bump `version` in
`recipe.json`, merge (or tag `v<version>` if you list at a tag `ref`) — the registry
re-resolves every `ref` on its next build (hourly, or on any push to `main` here) and
republishes. There is no PR here, no pointer to move and no review here; the honest
consequence is that on a branch `ref` your repo's maintainers are the only people between
a merge and every Butler. A template that should move only under review is listed with a
tag `ref`, and moving that `ref` to the next release IS a PR here.

A PR to this repo adds a template or removes one. Nothing else.

## Rules

- **One template per PR.** A registry PR adds exactly one `templates.json` entry (or
  removes one); do not bundle script, schema or doc changes with it. Keep the list sorted
  by name — `scripts/check_registry.py` fails otherwise.
- **The `ref` rule.** `ref` is a branch or a tag in your repo, and it is re-resolved on
  every build, not frozen. A branch follows whatever you merge, with no review here; a tag
  holds a release, and moving to the next release is a PR here changing the `ref` (a moved
  tag in your repo is followed too — the registry resolves the tag, it does not pin it).
  CI fails an entry whose `ref` does not resolve on the remote.
- **Repo URLs are `https://github.com/<owner>/<repo>`.** Never ssh, never another host,
  never a local path, and no credentials, query or fragment.
- **`recipe.json`'s `id` must equal the `name` this entry lists it as** — `--all`
  enforces this by cloning into a directory named after the registry entry.
- **Templates are text, never binaries, never external URLs.** No images, archives, or
  executables; no symlinks; no nested submodules; at most 50 files / 1 MB. The only URLs a
  template may reference are `github.com/Virtual-Protocol` /
  `raw.githubusercontent.com/Virtual-Protocol` links.
- **Bump `version`** for every change to an existing template — a plain integer, not
  semver. Publishing is immutable per `id@version` (`scripts/build_index.py` refuses to
  republish a version with different bytes), so changed content without a version bump
  fails the build rather than quietly republishing the old version number.
- **A money-moving template needs two maintainer reviews**, not one, on the PR that lists
  it. The registry publishes ONE index, so a merge to `main` reaches every Butler and
  there is no soak channel to land on first. Note what those reviews do and do not cover:
  they gate the template's admission, and its later versions only if it is listed at a
  tag `ref`.

## Before you open a PR

In your template repo — no registry checkout; the hub publishes its validator and replay
harness as standalone files, and a template's own CI runs the same two checks through
`uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page
```

Both must exit 0 with no infrastructure and no Butler account — see README.md for what
each checks (`replay.py` fetches `stub_bevo.py` and fixtures from the same site when they
are not beside it; keep the downloaded files out of the commit). Then tag (only if you
list at a tag `ref`): `git tag v<version> && git push origin main --tags`.

In your fork of this registry — only when you are adding the template (or removing it); a
new version of an already-listed template needs none of this. Add the entry to
`templates.json`, in name order:

```json
{ "name": "<id>", "repo": "https://github.com/<you>/butler-skill-<id>", "ref": "main" }
```

```bash
git add templates.json
git commit -m "templates: add <id>"
python3 scripts/check_registry.py        # the listing checks CI will run (--offline skips the remote ref check)
python3 scripts/validate.py --all        # add --maintainer for a butler- id (bevo- is refused)
python3 -m pytest tests -q               # the full local suite
```

## Review process

1. Open the PR from a fork; it changes only `templates.json`.
2. `validate.yml` runs `scripts/check_registry.py` on the listing (unique valid ids, an
   `https://github.com/<owner>/<repo>` URL with no credentials or query, a sane `ref`, and
   the `ref` resolving on the remote), the validator on every listed template, the replay
   tests, and a `build_index.py --dry-run` — it must be green before a human looks at the
   diff.
3. A `@Virtual-Protocol/butler-maintainers` review (two for a money-moving template) of
   the template **at the `ref` being listed** — the reviewer reads the template repo
   there — merges to `main`, which republishes the index immediately. Merging IS
   publishing: there is no second promotion step, and no per-environment view. A duty
   already created from a template pins the version it was created with (`env.RECIPE`),
   which is the only thing that does not move with the index.

## The trust boundary

The container installs a template by fetching the commit the build resolved
(`source.commit` in the index; the Pages-served files are the fallback with the same
bytes, and every file carries a sha256), so it can verify exactly what it fetched against
what the registry published.

What the registry does **not** do any more is freeze that content. It resolves each
entry's `ref` on every build, so on a branch `ref` anything the template repo merges —
including a force-push over that branch — is what the next build publishes, with no
review in this repo. The reviewed gate is admission to `templates.json`; after that,
write access to a listed template repo is effectively write access to what its template
does for every new duty created from it. Two consequences worth acting on: list a
template at a tag `ref` when its content should only move under review here, and treat
write access to a listed template repo with the same care as write access to this repo's
`main`.

## The yank rule

A skill is yanked or de-listed the same way (`scripts/remove_skill.py <name>`, with a
`name@X.Y.Z` spec in `yanked.json` for `--yank`); what that does to butlers that already
installed it is in [SECURITY.md](SECURITY.md). The rest of this section is about templates.

A published, broken or unsafe template is fixed forward by a new version — publishing is
immutable, so there is no "delete a version". A template is pulled from live use by
adding `"id@version"` to `yanked.json`; see [SECURITY.md](SECURITY.md) for the fast path.
Yanking by itself never removes the `templates.json` entry or touches a duty already
created from that ref — the container never re-fetches a template after `duty_create`.

Dropping a template from the registry outright is a separate decision, and it is deleting
its `templates.json` entry **plus** listing every published `id@version` of it in
`yanked.json`, so `scripts/build_index.py` keeps publishing a tombstone entry (no
`files`, no `source`) for every yanked version whose template is no longer listed — a
fresh `duty_create` against that ref then fails loudly instead of installing broken or
unsafe code. A yanked version superseded by a newer build of the same template needs no
tombstone: the newer entry supersedes it.

`scripts/remove_skill.py <name>` performs both removals so the choice is explicit: it
de-lists by default (a duty already created from the template keeps running) and
tombstones with `--yank` (a fresh `duty_create` against that ref is refused), resolving
the version to tombstone from the live index unless you pass `--version`. `--dry-run`
prints the change without writing. It is the mirror of `scripts/new_skill.py`:
registration is a template's first PR here, removal is its second and last, and every
version in between needs none.
