#!/usr/bin/env python3
"""new_skill.py <name> — scaffold a new skill from skills/_template/.

Usage:
    scripts/new_skill.py bevo-my-skill

Copies skills/_template/{SKILL.md,duty.py,CHANGELOG.md} into skills/<name>/,
substituting the placeholder name. Refuses reserved names (schema/reserved-names.json)
and the bevo- prefix unless --maintainer is passed. Leaves every TODO in
place — fill them in, then run scripts/validate.py.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
TEMPLATE_DIR = SKILLS_DIR / "_template"
RESERVED_PATH = REPO_ROOT / "schema" / "reserved-names.json"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new skill from skills/_template/.")
    parser.add_argument("name", help="skill directory / frontmatter name, e.g. bevo-my-skill")
    parser.add_argument("--maintainer", action="store_true", help="allow the bevo- prefix")
    args = parser.parse_args()

    name = args.name
    if not NAME_RE.match(name):
        parser.error(f"name must match ^[a-z0-9][a-z0-9-]{{1,63}}$, got {name!r}")

    reserved = set(json.loads(RESERVED_PATH.read_text()).get("reserved", []))
    if name in reserved:
        parser.error(f"{name!r} is a reserved name (schema/reserved-names.json)")
    if name.startswith("bevo-") and not args.maintainer:
        parser.error(f"{name!r} uses the maintainer-only 'bevo-' prefix; pass --maintainer if you are one")

    dest = SKILLS_DIR / name
    if dest.exists():
        parser.error(f"skills/{name}/ already exists")

    shutil.copytree(TEMPLATE_DIR, dest)
    for f in dest.iterdir():
        if f.is_file() and f.suffix in (".md", ".py"):
            text = f.read_text()
            text = text.replace("_template", name).replace("REPLACE_WITH_SKILL_NAME", name)
            f.write_text(text)

    print(f"created skills/{name}/")
    print("next: fill in every TODO, then run:")
    print(f"  python3 scripts/validate.py skills/{name}")
    print(f"  python3 tests/replay.py skills/{name} --fixture trade-activity-page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
