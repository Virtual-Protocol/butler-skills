#!/usr/bin/env python3
"""sync_readme.py — regenerate README.md's `bevo-copytrade` worked example
from the real skills/bevo-copytrade/{SKILL.md,duty.py}, so the README can
never drift from the shipped skill (and a Claude copying the README example
verbatim copies exactly what CI validates).

Usage:
    scripts/sync_readme.py             # rewrite README.md in place
    scripts/sync_readme.py --check     # exit 1 if README.md is out of sync, change nothing

The generated region is delimited by two HTML comments in README.md:
    <!-- BEGIN GENERATED: bevo-copytrade worked example (scripts/sync_readme.py; do not edit by hand) -->
    ...
    <!-- END GENERATED: bevo-copytrade worked example -->
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
SKILL_MD_PATH = REPO_ROOT / "skills" / "bevo-copytrade" / "SKILL.md"
DUTY_PY_PATH = REPO_ROOT / "skills" / "bevo-copytrade" / "duty.py"

BEGIN_MARKER = "<!-- BEGIN GENERATED: bevo-copytrade worked example (scripts/sync_readme.py; do not edit by hand) -->"
END_MARKER = "<!-- END GENERATED: bevo-copytrade worked example -->"


def build_block() -> str:
    skill_md = SKILL_MD_PATH.read_text()
    duty_py = DUTY_PY_PATH.read_text()

    parts = [
        "`skills/bevo-copytrade/SKILL.md`:",
        "",
        "````markdown",
        skill_md.rstrip("\n"),
        "````",
        "",
        "`skills/bevo-copytrade/duty.py`:",
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
        print("README.md is missing the BEGIN/END GENERATED markers for the bevo-copytrade worked example")
        return 1

    before, rest = readme.split(BEGIN_MARKER, 1)
    _, after = rest.split(END_MARKER, 1)

    new_block = build_block()
    new_readme = before + BEGIN_MARKER + "\n\n" + new_block + "\n" + END_MARKER + after

    if args.check:
        if new_readme != readme:
            print("README.md's bevo-copytrade worked example is out of sync with skills/bevo-copytrade/. Run scripts/sync_readme.py.")
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
