#!/usr/bin/env python3
"""check_registry.py — skills.json is the registry; check it.

The registry is a directory: one name and one GitHub link per skill. Nothing
about a skill's content lives here, so this checks the LISTING and the trust
boundary around it, not a pinned tree:

  (a) every entry has a valid skill name, unique across the file
  (b) `repo` is an `https://github.com/<owner>/<repo>` URL — no other host, no
      credentials, no query or fragment
  (c) `ref` is a plain branch or tag name
  (d) the ref actually resolves on the remote (`git ls-remote`), so a typo
      fails here rather than at publish time

What a container ends up trusting is the RESOLVED commit and per-file sha256
that build_index.py writes into the index, not this file — the link is how the
registry follows a skill, never what a butler executes.

Skipped with --offline (no network), which still runs (a) through (c).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "skills.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
REPO_RE = re.compile(r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def fail(msg: str) -> None:
    print(f"ERROR {msg}")


def ref_exists(repo: str, ref: str) -> bool:
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--heads", "--tags", repo, ref, f"refs/heads/{ref}", f"refs/tags/{ref}"],
            capture_output=True, text=True, check=True, timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return bool(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description="Check skills.json")
    ap.add_argument("--offline", action="store_true", help="skip the remote ref check")
    args = ap.parse_args()

    with open(REGISTRY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("skills")
    if not isinstance(rows, list) or not rows:
        fail("skills.json has no `skills` list")
        return 1

    problems = 0
    seen: set[str] = set()
    for row in rows:
        name, repo, ref = row.get("name"), row.get("repo"), row.get("ref") or "main"
        if not NAME_RE.match(str(name or "")):
            fail(f"{name!r} is not a valid skill name"); problems += 1; continue
        if name in seen:
            fail(f"{name}: listed twice"); problems += 1; continue
        seen.add(name)
        parts = urlsplit(str(repo or ""))
        if not REPO_RE.match(str(repo or "")) or parts.query or parts.fragment or "@" in parts.netloc:
            fail(f"{name}: repo must be https://github.com/<owner>/<repo>, got {repo!r}"); problems += 1; continue
        if not REF_RE.match(str(ref)):
            fail(f"{name}: bad ref {ref!r}"); problems += 1; continue
        if not args.offline and not ref_exists(repo, ref):
            fail(f"{name}: {repo} has no ref {ref!r}"); problems += 1; continue
        print(f"OK    {name} = {repo} @ {ref}")

    if sorted(seen) != [r.get("name") for r in rows]:
        fail("skills.json is not sorted by name"); problems += 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
