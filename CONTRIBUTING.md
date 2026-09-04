# Contributing

This repo is the **registry** of `SKILL.md` playbooks that Butler containers read directly.
Every skill is its own git repository; the registry is a directory of links — `skills.json`
lists each skill by `name`, `repo` and `ref`, and every build clones each entry at its `ref`
and publishes an index of the commits those refs resolved to. If you are a developer's
Claude session with only this repo's README URL,
start there: [README.md](README.md) — "Building a skill: the 8 steps". This file is the
condensed process/trust rules; README.md is the how-to.

## The standard

Everything CI enforces is written out in [SKILL_STANDARD.md](SKILL_STANDARD.md) — that file
mirrors exactly what `scripts/validate.py` (and, for the registry listing,
`scripts/check_registry.py`) checks. If the two ever disagree, the validator is right and `SKILL_STANDARD.md` has drifted
(please file a PR fixing the doc).

## Where things live

| What | Where |
| --- | --- |
| A skill's files (`SKILL.md`, `duty.py`, `CHANGELOG.md`) | the skill's own repo, at its root — created from [`Virtual-Protocol/butler-skill-template`](https://github.com/Virtual-Protocol/butler-skill-template) |
| Team skills | `Virtual-Protocol/butler-skill-<name>` |
| Community skills | the author's own GitHub repo |
| The registry entry | this repo: one `{"name", "repo", "ref"}` row in `skills.json` — the whole of what the registry stores about a skill |
| The published index | GitHub Pages, built by `scripts/build_index.py`, which clones each entry at its `ref` into a temp directory and indexes that throwaway checkout |
| The standalone tools (`validate.py`, `replay.py`, `stub_bevo.py`, `check_selectors.mjs`, fixtures) | GitHub Pages `tools/`, laid out by `scripts/publish_tools.py`; skill-repo CI is the composite action `.github/actions/validate` |

No skill content lives in this repo, so there is nothing here to edit for a skill change.

## Publishing an update to a skill already in the registry

**Merge it in your own skill repo. That is the whole procedure.** Bump `version`, add the
`CHANGELOG.md` line, tag `v<version>`, merge — the registry re-resolves every `ref` on its
next build (hourly, or on any push to `main` here) and republishes. There is no PR here, no
pointer to move and no review here; the honest consequence is that on a branch `ref` your
repo's maintainers are the only people between a merge and every Butler. A skill that should
move only under review is listed with a tag `ref`, and moving that `ref` to the next release
IS a PR here.

A PR to this repo adds a skill or removes one. Nothing else.

## Rules

- **One skill per PR.** A registry PR adds exactly one `skills.json` entry (or removes
  one); do not bundle script, schema or doc changes with it. Keep the list sorted by name —
  `scripts/check_registry.py` fails otherwise.
- **The `ref` rule.** `ref` is a branch or a tag in your repo, and it is re-resolved on
  every build, not frozen. A branch follows whatever you merge, with no review here; a tag
  holds a release, and moving to the next release is a PR here changing the `ref` (a moved
  tag in your repo is followed too — the registry resolves the tag, it does not pin it).
  CI fails an entry whose `ref` does not resolve on the remote.
- **Repo URLs are `https://github.com/<owner>/<repo>`.** Never ssh, never another host,
  never a local path, and no credentials, query or fragment.
- **Skills are text, never binaries, never external URLs.** No images, archives, or
  executables; no symlinks; no nested submodules; at most 50 files / 1 MB (the container
  refuses a clone that breaks any of these). The only URLs a skill may reference are
  `{API_BASE}` placeholders (resolved by the container) and links to
  `github.com/Virtual-Protocol` / `raw.githubusercontent.com/Virtual-Protocol`.
- **Bump the version and add a `CHANGELOG.md` line** for every change to an existing
  skill — semver, tagged `vX.Y.Z` in the skill repo. Publishing is immutable per
  `name@version` (`scripts/build_index.py` refuses to republish a version with different
  bytes), so changed content without a version bump fails the build rather than quietly
  republishing the old version number.
- **DCO sign-off is required** on every commit in the registry PR (`git commit -s`). CI
  checks this on every PR.
- **Money-moving skills (`moneyMoving: true`) need two maintainer reviews**, not one —
  on the PR that lists the skill. The registry publishes ONE index, so a merge to `main`
  reaches every Butler and there is no soak channel to land on first. Note what those
  reviews do and do not cover: they gate the skill's admission, and its later versions only
  if it is listed at a tag `ref`.

## Before you open a PR

In your skill repo — no registry checkout; the hub publishes its validator and replay
harness as standalone files, and the template's CI runs the same two checks through
`uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page
```

Both must exit 0 with no infrastructure and no Butler account — see README.md §7 for what
each checks (`replay.py` fetches `stub_bevo.py` and fixtures from the same site when they
are not beside it; keep the downloaded files out of the commit). Then tag:
`git tag v<version> && git push origin main --tags`.

In your fork of this registry — only when you are adding the skill (or removing it); a
new version of an already-listed skill needs none of this. Add the entry to `skills.json`,
in name order:

```json
{ "name": "<name>", "repo": "https://github.com/<you>/butler-skill-<name>", "ref": "main" }
```

```bash
git add skills.json
git commit -s -m "skills: add <name>"
python3 scripts/check_registry.py        # the listing checks CI will run (--offline skips the remote ref check)
python3 scripts/validate.py --all        # add --maintainer for a butler- skill (bevo- is refused)
python3 -m pytest tests -q               # the full local suite
```

## Review process

1. Open the PR from a fork with DCO sign-off; it changes only `skills.json`.
2. `validate.yml` runs `scripts/check_registry.py` on the listing (unique valid names, an
   `https://github.com/<owner>/<repo>` URL with no credentials or query, a sane `ref`, and
   the `ref` resolving on the remote), the validator on every listed skill, the replay tests,
   a `build_index.py --dry-run` and the DCO check — it must be green before a human looks at
   the diff.
3. A `@Virtual-Protocol/butler-maintainers` review (two for `moneyMoving:true`) of the skill
   **at the `ref` being listed** — the reviewer reads the skill repo there — merges to
   `main`, which republishes the index immediately. Merging IS publishing: there is no second
   promotion step, and no per-environment view. An owner who wants to hold a version pins it
   (`bevo-hub install <name>@<version>`), which is the only thing that does not move with the
   index.

## The trust boundary

Butler installs a skill by cloning the commit the build resolved (`source.commit` in the
index; the Pages-served files are the fallback with the same bytes, and every file carries a
sha256), so a container can verify exactly what it fetched against what the registry
published.

What the registry does **not** do any more is freeze that content. It resolves each entry's
`ref` on every build, so on a branch `ref` anything the skill repo merges — including a
force-push over that branch — is what the next build publishes, with no review in this repo.
The reviewed gate is admission to `skills.json`; after that, write access to a listed skill
repo is effectively write access to what its skill does on every Butler. Two consequences
worth acting on: list a skill at a tag `ref` when its content should only move under review
here, and treat write access to a listed skill repo with the same care as write access to
this repo's `main`.

## The yank rule

A published, broken or unsafe skill is fixed forward by a new version — publishing is
immutable, so there is no "delete a version". A skill is pulled from live use by adding
`"name@version"` to `yanked.json`; see [SECURITY.md](SECURITY.md) for the fast path.
Yanking by itself never removes the `skills.json` entry or rewrites the skill repo (a
rename would make OpenClaw's `discover_skills` treat it as a brand-new, un-yanked skill).

Dropping a skill from the registry outright is a separate decision, and it is deleting its
`skills.json` entry **plus** listing every published `name@version` of it in `yanked.json`.
The container's hub client only disables a skill on an index entry that carries
`yanked:true` — a skill that merely disappears from the index stays installed and enabled —
so `scripts/build_index.py` keeps publishing a `yanked:true` tombstone entry (no `files`,
no `source`) for every yanked version whose skill is no longer listed. A yanked version
superseded by a newer build of the same skill needs no tombstone: the newer entry un-yanks
and updates the container.
