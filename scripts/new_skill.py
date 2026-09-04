#!/usr/bin/env python3
"""new_skill.py <name> — print the exact commands to start a new skill.

Usage:
    scripts/new_skill.py my-skill [--owner <github-user-or-org>] [--maintainer]

Skills are git-backed: each one is its own repository created from the
GitHub template Virtual-Protocol/butler-skill-template, and this registry pins
it as a submodule under skills/<name> at a tagged commit. So this script no
longer scaffolds a directory here — it checks the name (pattern, reserved
list, the maintainer-only butler- prefix; bevo- is the container's bundled-skill
namespace and is refused outright) and prints, in order:

  1. the `gh repo create ... --template ... --clone` command,
  2. the validator + offline replay commands to run in that checkout,
  3. the tag + `git submodule add` PR step that lands it in this registry.

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

TEMPLATE_REPO = "Virtual-Protocol/butler-skill-template"
REGISTRY_REPO = "Virtual-Protocol/butler-skills"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MAINTAINER_PREFIX = "butler-"
CONTAINER_PREFIX = "bevo-"
TOOLS_URL = "https://virtual-protocol.github.io/butler-skills/tools"


def repo_name_for(name: str) -> str:
    """Skill `butler-copytrade` lives in `butler-skill-copytrade`; a community
    skill `my-dca` lives in `butler-skill-my-dca`."""
    base = name[len(MAINTAINER_PREFIX):] if name.startswith(MAINTAINER_PREFIX) else name
    return f"butler-skill-{base}"


def render(name: str, owner: str, repo_root: Path = REPO_ROOT) -> str:
    repo = repo_name_for(name)
    return f"""\
# {name} — a Butler skill is its own git repository. Three steps:

# 1. Create your repo from the template (GitHub: "Use this template" does the same):
gh repo create {owner}/{repo} --template {TEMPLATE_REPO} --public --clone
cd {repo}
#    Set `name: {name}` in SKILL.md's frontmatter, then replace every TODO in
#    SKILL.md, duty.py and CHANGELOG.md (the validator refuses any TODO left in place).

# 2. Validate and replay locally — no Butler account, container or registry checkout
#    needed; the hub publishes its validator and replay harness as standalone files
#    (replay.py downloads stub_bevo.py and any fixture it needs). The template's CI runs
#    the same two checks as one step: `uses: {REGISTRY_REPO}/.github/actions/validate@main`.
curl -sSLO {TOOLS_URL}/validate.py
curl -sSLO {TOOLS_URL}/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page

# 3. Tag the release, then open a PR to {REGISTRY_REPO} that pins it:
git tag v1.0.0 && git push origin main --tags
#    in a fork/checkout of {REGISTRY_REPO}:
git submodule add https://github.com/{owner}/{repo} skills/{name}
git -C skills/{name} checkout v1.0.0
git add .gitmodules skills/{name}
git commit -s -m "skills: add {name} 1.0.0"
gh pr create --repo {REGISTRY_REPO} --base main --title "skills: add {name} 1.0.0"
#    CI validates the pinned checkout and asserts the pinned commit carries tag
#    v<version>; maintainers review that commit (two reviews if moneyMoving:true).
#    A later version = bump `version`, add a CHANGELOG line, tag vX.Y.Z, and a PR
#    moving the submodule pointer to that tag.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the commands to create, validate and register a new skill.")
    parser.add_argument("name", help="skill name (the frontmatter `name` and the registry path skills/<name>), e.g. my-dca")
    parser.add_argument("--owner", default="<you>", help="GitHub user/org that will own the skill repo (default: a <you> placeholder)")
    parser.add_argument("--maintainer", action="store_true", help="allow the maintainer-only butler- prefix (bevo- is always refused)")
    args = parser.parse_args()

    name = args.name
    if not NAME_RE.match(name):
        parser.error(f"name must match ^[a-z0-9][a-z0-9-]{{1,63}}$, got {name!r}")

    reserved = set(json.loads(RESERVED_PATH.read_text()).get("reserved", []))
    if name in reserved:
        parser.error(f"{name!r} is a reserved name (schema/reserved-names.json)")
    if name.startswith(CONTAINER_PREFIX):
        parser.error(
            f"{name!r} uses the '{CONTAINER_PREFIX}' prefix — that is the container's bundled-skill namespace "
            f"(bevo-hub, bevo-onchain, bevo-automation-creator, ...); hub skills may never use it. "
            f"Team skills use '{MAINTAINER_PREFIX}'"
        )
    if name.startswith(MAINTAINER_PREFIX) and not args.maintainer:
        parser.error(f"{name!r} uses the maintainer-only '{MAINTAINER_PREFIX}' prefix; pass --maintainer if you are one")

    sys.stdout.write(render(name, args.owner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
