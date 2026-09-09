#!/usr/bin/env python3
"""check_dco.py — verify every commit in a PR carries a DCO sign-off line
("Signed-off-by: Name <email>"). Used by .github/workflows/validate.yml.

Usage:
    scripts/check_dco.py --base origin/main --head HEAD
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

SIGNOFF_RE = re.compile(r"^Signed-off-by: .+ <.+@.+>$", re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    # --no-merges: on a pull_request run, HEAD may be GitHub's synthetic merge
    # commit (refs/pull/N/merge), which never carries a sign-off and is not a
    # commit the author wrote. Only the author's own commits are checked.
    out = subprocess.run(
        ["git", "log", "--no-merges", f"{args.base}..{args.head}", "--pretty=format:%H"],
        capture_output=True, text=True, check=True,
    )
    shas = [s for s in out.stdout.splitlines() if s.strip()]
    if not shas:
        print("no commits to check")
        return 0

    bad = []
    for sha in shas:
        msg = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%B", sha],
            capture_output=True, text=True, check=True,
        ).stdout
        if not SIGNOFF_RE.search(msg):
            bad.append(sha)

    if bad:
        print("missing DCO sign-off (Signed-off-by: Name <email>) on:")
        for sha in bad:
            print(f"  {sha}")
        print("fix with: git commit --amend -s   (or git rebase --exec 'git commit --amend --no-edit -s')")
        return 1

    print(f"DCO sign-off present on all {len(shas)} commit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
