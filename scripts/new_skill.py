#!/usr/bin/env python3
"""new_skill.py <name> — print the exact commands to start a new duty template or skill.

Usage:
    scripts/new_skill.py my-dca [--owner <github-user-or-org>] [--maintainer]            # a duty template
    scripts/new_skill.py my-skill --skill [--owner <github-user-or-org>] [--maintainer]  # a skill

Both kinds are git-backed: each one is its own repository, and this registry
lists it as a name, a link and a ref — a duty template in templates.json
(recipe.json, duty.py and README.md at the repo root), a skill in skills.json
(SKILL.md, README.md and CHANGELOG.md). So this script does not scaffold a
directory here — it checks the name (pattern, reserved list, the
maintainer-only butler- prefix; bevo- is the container's bundled-command
namespace and is refused outright) and prints, in order:

  1. the commands to create the repo and what to lay out in it,
  2. the validator (and, for a template, the offline replay) to run there,
  3. the one-line listing PR that lands it in this registry.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESERVED_PATH = REPO_ROOT / "schema" / "reserved-names.json"

REGISTRY_REPO = "Virtual-Protocol/butler-skills"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
# Mastra's skill-name rule; a skill that breaks it is silently dropped.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAINTAINER_PREFIX = "butler-"
CONTAINER_PREFIX = "bevo-"
TOOLS_URL = "https://virtual-protocol.github.io/butler-skills/tools"


def repo_name_for(name: str) -> str:
    """Template `copytrade` lives in `butler-skill-copytrade`; a community
    template `my-dca` lives in `butler-skill-my-dca`. Skills follow the same rule."""
    base = name[len(MAINTAINER_PREFIX):] if name.startswith(MAINTAINER_PREFIX) else name
    return f"butler-skill-{base}"


def render(name: str, owner: str) -> str:
    repo = repo_name_for(name)
    return f"""\
# {name} — a duty template is its own git repository: recipe.json, duty.py and
# README.md at its root. Three steps:

# 1. Create your repo (any git host works; GitHub is what the registry links to):
gh repo create {owner}/{repo} --public --clone
cd {repo}
#    Write recipe.json (id "{name}", an integer version, a description, the
#    trigger kinds it accepts, and a params JSON-Schema), duty.py (the whole
#    program — waiters, `bevo.*` reads, and money through a shelled
#    `acp trade ... --idempotency-key ...`) and README.md.

# 2. Validate locally — no Butler account, container or registry checkout
#    needed; the hub publishes its validator and replay harness as standalone files
#    (replay.py downloads stub_bevo.py and any fixture it needs). A template's CI
#    runs the same two checks as one step: `uses: {REGISTRY_REPO}/.github/actions/validate@main`.
curl -sSLO {TOOLS_URL}/validate.py
curl -sSLO {TOOLS_URL}/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page

# 3. Open a PR to {REGISTRY_REPO} adding one entry to templates.json:
#    in a fork/checkout of {REGISTRY_REPO}, add to the "templates" list (keep it sorted by name):
#      {{"name": "{name}", "repo": "https://github.com/{owner}/{repo}", "ref": "main"}}
#    `ref` is what the registry follows. A branch means every commit you merge
#    reaches butlers on the next build (hourly) with no review in the registry;
#    tag your releases and set `ref` to the tag (e.g. "v1") if you want it to
#    move only when you say so.
git commit -m "templates: add {name}"
gh pr create --repo {REGISTRY_REPO} --base main --title "templates: add {name}"
#    CI checks the listing (name, https GitHub URL, the ref resolves) and the
#    publish build clones your ref, hashes every file and records the resolved
#    commit in the index. Maintainers review the entry. After that, a new
#    version is a release in YOUR repo — bump `version` in recipe.json, merge
#    (or tag, if `ref` is a tag). You never open another PR here unless the
#    template is removed.
"""


def render_skill(name: str, owner: str) -> str:
    repo = repo_name_for(name)
    return f"""\
# {name} — a skill is its own git repository: SKILL.md, README.md and
# CHANGELOG.md at its root (SKILL_STANDARD.md in {REGISTRY_REPO} is the checklist).
# Three steps:

# 1. Create the repo:
gh repo create {owner}/{repo} --public --clone
cd {repo}
#    Write SKILL.md — frontmatter `name: {name}`, `version: 1.0.0`, a one-line
#    `description` and ONE line of JSON `metadata` ({{"butler": {{"moneyMoving", "keywords",
#    "requires": {{"bins"}}}}}}), then the seven sections, in order — plus README.md (for
#    humans) and CHANGELOG.md with a "## 1.0.0" entry. Only SKILL.md and
#    references/**/*.md are published to butlers.

# 2. Validate locally — no Butler account, container or registry checkout needed. A
#    skill's CI is the same check as one step:
#    `uses: {REGISTRY_REPO}/.github/actions/validate@main` (with `maintainer: "true"` for a butler- name).
curl -sSLO {TOOLS_URL}/validate.py
python3 validate.py --standalone .

# 3. A maintainer lists it: one entry in skills.json (keep it sorted by name):
#      {{"name": "{name}", "repo": "https://github.com/{owner}/{repo}", "ref": "main"}}
git commit -m "skills: add {name}"
gh pr create --repo {REGISTRY_REPO} --base main --title "skills: add {name}"
#    Listing a skill is maintainer-only, and a moneyMoving skill needs two maintainer
#    reviews. Every build validates it again and fails outright on any error. After
#    that, a new version is a release in YOUR repo — bump `version` in SKILL.md, add
#    its CHANGELOG.md entry, merge. Publishing is immutable per name@version: changed
#    bytes under a published version fail the build.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the commands to create, validate and register a new duty template or skill.")
    parser.add_argument("name", help="the registry name (recipe.json's `id`, or SKILL.md's `name`), e.g. my-dca")
    parser.add_argument("--skill", action="store_true", help="a skill (SKILL.md, listed in skills.json) instead of a duty template")
    parser.add_argument("--owner", default="<you>", help="GitHub user/org that will own the repo (default: a <you> placeholder)")
    parser.add_argument("--maintainer", action="store_true", help="allow the maintainer-only butler- prefix (bevo- is always refused)")
    args = parser.parse_args()

    name = args.name
    if args.skill:
        if not SKILL_NAME_RE.match(name) or len(name) > 64:
            parser.error(f"a skill name must match ^[a-z0-9]+(-[a-z0-9]+)*$ (at most 64 chars), got {name!r}")
    elif not NAME_RE.match(name):
        parser.error(f"id must match ^[a-z0-9][a-z0-9-]{{1,63}}$, got {name!r}")

    reserved = set(json.loads(RESERVED_PATH.read_text()).get("reserved", []))
    if name in reserved:
        parser.error(f"{name!r} is a reserved name (schema/reserved-names.json)")
    if name.startswith(CONTAINER_PREFIX):
        parser.error(
            f"{name!r} uses the '{CONTAINER_PREFIX}' prefix — that is the container's bundled-command "
            f"namespace; nothing in the hub may use it. Team entries use '{MAINTAINER_PREFIX}'"
        )
    if name.startswith(MAINTAINER_PREFIX) and not args.maintainer:
        parser.error(f"{name!r} uses the maintainer-only '{MAINTAINER_PREFIX}' prefix; pass --maintainer if you are one")

    sys.stdout.write(render_skill(name, args.owner) if args.skill else render(name, args.owner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
