# Contributing

This repo publishes `SKILL.md` playbooks that Butler containers read directly. If you are a
developer's Claude session with only this repo's README URL, start there:
[README.md](README.md) — "Building a skill: the 8 steps". This file is the condensed
process/trust rules; README.md is the how-to.

## The standard

Everything CI enforces is written out in [SKILL_STANDARD.md](SKILL_STANDARD.md) — that file
mirrors exactly what `scripts/validate.py` checks. If the two ever disagree, the validator
is right and `SKILL_STANDARD.md` has drifted (please file a PR fixing the doc).

## Rules

- **One skill per PR.** Do not bundle unrelated skills, script changes, or schema changes
  into the same PR as a new/updated skill.
- **Skills are text, never binaries, never external URLs.** No images, archives, or
  executables under `skills/**`; the only URLs a skill may reference are `{API_BASE}`
  placeholders (resolved by the container) and links to
  `github.com/Virtual-Protocol` / `raw.githubusercontent.com/Virtual-Protocol`.
- **Bump the version and add a `CHANGELOG.md` line** for every change to an existing
  skill — semver, and the directory is re-published immutably per version
  (`scripts/build_index.py` refuses to overwrite a published version with different bytes).
- **DCO sign-off is required.** Every commit needs `Signed-off-by: Name <email>` (`git commit
  -s`). CI checks this on every PR.
- **Money-moving skills (`moneyMoving: true`) need two maintainer reviews**, not one.
  External (non-maintainer) contributions land on the `canary` channel first regardless of
  review count, and promote to `stable` on the next tag.

## Before you open a PR

```bash
python3 scripts/validate.py skills/<name>
python3 tests/replay.py skills/<name> --fixture trade-activity-page
```

Both must exit 0 with no infrastructure and no Bevo account — see README.md §7 for what
each checks. `python3 -m pytest tests -q` runs the full local suite including fixture-skill
regression tests.

## Review process

1. Open the PR from a fork with DCO sign-off.
2. `validate.yml` runs the same validator plus the replay tests and the DCO check — it must
   be green before a human looks at the diff.
3. A `@Virtual-Protocol/butler-maintainers` review (two for `moneyMoving:true`) merges to
   `main`, which republishes the `canary` channel immediately.
4. A maintainer promotes to `stable` by tagging `vYYYY.MM.DD[.N]` once the change has been
   soaked on `canary`.

## The yank rule

A published, broken or unsafe skill is fixed forward by a new version — publishing is
immutable, so there is no "delete a version". A skill is pulled from live use by adding
`"name@version"` to `yanked.json`; see [SECURITY.md](SECURITY.md) for the fast path.
Yanking never rewrites or deletes the skill's directory (a rename would make OpenClaw's
`discover_skills` treat it as a brand-new, un-yanked skill).
