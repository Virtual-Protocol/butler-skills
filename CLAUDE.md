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
- Inside a skill repo: `python3 <butler-skills>/scripts/validate.py --standalone .` and
  `python3 <butler-skills>/tests/replay.py --standalone . --fixture trade-activity-page`.
