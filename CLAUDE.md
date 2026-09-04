# CLAUDE.md

Read README.md first. This repo is a **registry**: every skill is its own git repository
(created from `Virtual-Protocol/butler-skill-template`), pinned here as a submodule at
`skills/<name>` at the commit of its `v<version>` tag. Nothing under `skills/` is edited
in this repo — a skill change is a new tag in the skill repo plus a PR here that moves the
submodule pointer (`.gitmodules` + the gitlink).

- After cloning / switching branches: `git submodule update --init --recursive`.
- Before every commit here: `python3 scripts/validate.py --all --maintainer`,
  `python3 scripts/check_pins.py`, `python3 -m pytest tests -q`,
  `python3 scripts/sync_readme.py --check`.
- Inside a skill repo (no registry checkout — the tools are published standalone):
  `curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py`,
  `curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py`, then
  `python3 validate.py --standalone .` and
  `python3 replay.py --standalone . --fixture trade-activity-page`. Skill-repo CI is the
  composite action `.github/actions/validate` (`uses: Virtual-Protocol/butler-skills/.github/actions/validate@main`);
  `scripts/publish_tools.py` lays the tools out under `dist/tools/` for Pages.
- Name prefixes: `butler-` is maintainer-only; `bevo-` is the container's bundled-skill
  namespace and is refused. A skill is the delta over AGENTS.md — never restate what the
  container already teaches.
