#!/usr/bin/env python3
"""sync_readme.py — regenerate README.md's `butler-copytrade` worked example
from that skill's own repository, so the README can never drift from what the
registry publishes (and a Claude copying the README example verbatim copies
exactly what CI validates).

No skill is checked out here: skills.json records `butler-copytrade`'s repo
and the ref the registry follows, and this script clones that ref into a
temporary directory and reads SKILL.md and duty.py out of it — the same
resolution the publish build does. It therefore needs the network, and its
answer moves when the skill repo moves: if the ref is a branch, a merge over
there makes `--check` fail here until the block is regenerated.

Usage:
    scripts/sync_readme.py             # rewrite README.md in place
    scripts/sync_readme.py --check     # exit 1 if README.md is out of sync, change nothing

The generated region is delimited by two HTML comments in README.md:
    <!-- BEGIN GENERATED: butler-copytrade worked example (scripts/sync_readme.py; do not edit by hand) -->
    ...
    <!-- END GENERATED: butler-copytrade worked example -->
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
REGISTRY_PATH = REPO_ROOT / "skills.json"
SKILL_NAME = "butler-copytrade"

BEGIN_MARKER = "<!-- BEGIN GENERATED: butler-copytrade worked example (scripts/sync_readme.py; do not edit by hand) -->"
END_MARKER = "<!-- END GENERATED: butler-copytrade worked example -->"


def registry_entry(name: str = SKILL_NAME) -> dict:
    """The skills.json row for `name` — the repo link and the ref to clone."""
    rows = json.loads(REGISTRY_PATH.read_text()).get("skills", [])
    for row in rows:
        if row.get("name") == name:
            return row
    raise SystemExit(f"{name} is not listed in {REGISTRY_PATH}")


def clone(entry: dict, dest: Path) -> Path:
    repo, ref = entry["repo"], entry.get("ref") or "main"
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--branch", ref, repo, str(dest)],
            capture_output=True, text=True, check=True, timeout=300,
        )
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"cannot clone {repo} at {ref!r}: {e.stderr.strip() or e}")
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"cannot clone {repo} at {ref!r}: {e}")
    return dest


def build_block(skill_dir: Path, entry: dict) -> str:
    skill_md_path = skill_dir / "SKILL.md"
    duty_py_path = skill_dir / "duty.py"
    if not skill_md_path.exists():
        raise SystemExit(f"{entry['repo']} at {entry.get('ref')!r} has no SKILL.md at its root")
    skill_md = skill_md_path.read_text()
    duty_py = duty_py_path.read_text()
    origin = f" (from {entry['repo']} at `{entry.get('ref') or 'main'}`)"

    parts = [
        f"`{SKILL_NAME}/SKILL.md`{origin}:",
        "",
        "````markdown",
        skill_md.rstrip("\n"),
        "````",
        "",
        f"`{SKILL_NAME}/duty.py`:",
        "",
        "```python",
        duty_py.rstrip("\n"),
        "```",
        "",
    ]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if README.md is out of sync; write nothing")
    args = parser.parse_args()

    readme = README_PATH.read_text()
    if BEGIN_MARKER not in readme or END_MARKER not in readme:
        print("README.md is missing the BEGIN/END GENERATED markers for the butler-copytrade worked example")
        return 1

    before, rest = readme.split(BEGIN_MARKER, 1)
    _, after = rest.split(END_MARKER, 1)

    entry = registry_entry()
    with tempfile.TemporaryDirectory(prefix="butler-skills-readme-") as tmp:
        new_block = build_block(clone(entry, Path(tmp) / SKILL_NAME), entry)
    new_readme = before + BEGIN_MARKER + "\n\n" + new_block + "\n" + END_MARKER + after

    if args.check:
        if new_readme != readme:
            print(
                f"README.md's {SKILL_NAME} worked example is out of sync with "
                f"{entry['repo']} at {entry.get('ref')!r}. Run scripts/sync_readme.py."
            )
            return 1
        print("README.md is in sync")
        return 0

    if new_readme != readme:
        README_PATH.write_text(new_readme)
        print("wrote README.md")
    else:
        print("README.md already in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
