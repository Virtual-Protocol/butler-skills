# Contributing

This repo is the **registry** of `SKILL.md` playbooks that Butler containers read directly.
Every skill is its own git repository; the registry pins each one as a git submodule at
`skills/<name>`, at the commit of the skill repo's `v<version>` tag, and publishes an index
of those pins. If you are a developer's Claude session with only this repo's README URL,
start there: [README.md](README.md) — "Building a skill: the 8 steps". This file is the
condensed process/trust rules; README.md is the how-to.

## The standard

Everything CI enforces is written out in [SKILL_STANDARD.md](SKILL_STANDARD.md) — that file
mirrors exactly what `scripts/validate.py` (and, for the pin rules, `scripts/check_pins.py`)
checks. If the two ever disagree, the validator is right and `SKILL_STANDARD.md` has drifted
(please file a PR fixing the doc).

## Where things live

| What | Where |
| --- | --- |
| A skill's files (`SKILL.md`, `duty.py`, `CHANGELOG.md`) | the skill's own repo, at its root — created from [`Virtual-Protocol/butler-skill-template`](https://github.com/Virtual-Protocol/butler-skill-template) |
| Team skills | `Virtual-Protocol/butler-skill-<name>` |
| Community skills | the author's own GitHub repo |
| The pin | this repo: `.gitmodules` + the `skills/<name>` gitlink, always at a tagged commit |
| The published index | GitHub Pages, built by `scripts/build_index.py` from the pinned checkouts |
| The standalone tools (`validate.py`, `replay.py`, `stub_bevo.py`, `check_selectors.mjs`, fixtures) | GitHub Pages `tools/`, laid out by `scripts/publish_tools.py`; skill-repo CI is the composite action `.github/actions/validate` |

Nothing under `skills/` is ever edited in a PR to this repo — a change to a skill is a
commit and a tag in the skill repo, then a PR here that moves the pointer.

## Rules

- **One skill per PR.** A registry PR adds or moves exactly one submodule pointer
  (`.gitmodules` + `skills/<name>`); do not bundle script, schema or doc changes with it.
- **The tag rule.** The pinned commit must carry the tag `v<version>` in the skill repo,
  where `<version>` is the frontmatter `version` of the pinned `SKILL.md`. CI asserts this
  (`git -C skills/<name> tag --points-at HEAD` contains `v<version>`). Tags are immutable
  by convention: a change is a new version, never a moved tag (moving it cannot change the
  pin anyway — the registry holds the commit hash, not the tag).
- **Submodule URLs are `https://github.com/<owner>/<repo>`.** Never ssh, never another
  host, never a local path.
- **Skills are text, never binaries, never external URLs.** No images, archives, or
  executables; no symlinks; no nested submodules; at most 50 files / 1 MB (the container
  refuses a clone that breaks any of these). The only URLs a skill may reference are
  `{API_BASE}` placeholders (resolved by the container) and links to
  `github.com/Virtual-Protocol` / `raw.githubusercontent.com/Virtual-Protocol`.
- **Bump the version and add a `CHANGELOG.md` line** for every change to an existing
  skill — semver, tagged `vX.Y.Z` in the skill repo. Publishing is immutable per
  `name@version` (`scripts/build_index.py` refuses to republish a version with different
  bytes), so a moved pointer without a version bump fails CI.
- **DCO sign-off is required** on every commit in the registry PR (`git commit -s`). CI
  checks this on every PR.
- **Money-moving skills (`moneyMoving: true`) need two maintainer reviews**, not one.
  Review is the only gate: the registry publishes ONE index, so a merge to `main` reaches
  every Butler. There is no soak channel to land on first.

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

In your fork of this registry:

```bash
git submodule add https://github.com/<you>/butler-skill-<name> skills/<name>   # first release only
git -C skills/<name> fetch --tags && git -C skills/<name> checkout v<version>   # every release
git add .gitmodules skills/<name>
git commit -s -m "skills: <name> <version>"
python3 scripts/check_pins.py            # the pin rules CI will run
python3 scripts/validate.py --all        # add --maintainer for a butler- skill (bevo- is refused)
python3 -m pytest tests -q               # the full local suite
```

## Review process

1. Open the PR from a fork with DCO sign-off; it changes only `.gitmodules` and
   `skills/<name>`.
2. `validate.yml` checks out the pinned commit (`submodules: recursive`), runs
   `scripts/check_pins.py` (tag `v<version>` at the pinned commit, https GitHub URL, no
   symlinks/nested submodules), the validator on every pinned skill, the replay tests and
   the DCO check — it must be green before a human looks at the diff.
3. A `@Virtual-Protocol/butler-maintainers` review (two for `moneyMoving:true`) of the
   **pinned content** — the reviewer reads the skill repo at that commit, which is exactly
   what Butler will clone — merges to `main`, which republishes the index immediately.
   Merging IS publishing: there is no second promotion step, and no per-environment view.
   An owner who wants to hold a version pins it (`bevo-hub install <name>@<version>`).

## The trust boundary

Butler installs a skill by cloning the pinned commit (`source.commit` in the index; the
Pages-served files are the fallback with the same bytes). The registry — not the skill
repo — decides what runs: a force-push, a moved tag or a deleted branch in a skill repo
cannot change a pin, and moving a pin is a reviewed PR here. Do not grant anyone write
access to this repo's `main` on the strength of write access to a skill repo.

## The yank rule

A published, broken or unsafe skill is fixed forward by a new version — publishing is
immutable, so there is no "delete a version". A skill is pulled from live use by adding
`"name@version"` to `yanked.json`; see [SECURITY.md](SECURITY.md) for the fast path.
Yanking by itself never removes the submodule or rewrites the skill repo (a rename would
make OpenClaw's `discover_skills` treat it as a brand-new, un-yanked skill; retagging
changes nothing the registry pins).

Dropping a skill from the registry outright is a separate decision, and it is `git rm` of
the submodule **plus** every published `name@version` of it in `yanked.json`. The
container's hub client only disables a skill on an index entry that carries
`yanked:true` — a skill that merely disappears from the index stays installed and enabled —
so `scripts/build_index.py` keeps publishing a `yanked:true` tombstone entry (no `files`,
no `source`) for every yanked version whose skill no longer has a submodule. A yanked
version superseded by a newer pin of the same skill needs no tombstone: the newer entry
un-yanks and updates the container.
