#!/usr/bin/env python3
"""check_registry.py — templates.json and skills.json are the registry; check them.

The registry is a directory: one name and one GitHub link per duty template
(templates.json) and per skill (skills.json). Nothing about their content
lives here, so this checks the LISTINGS and the trust boundary around them,
not a pinned tree:

  (a) every entry has a valid name — a template id in templates.json, a
      Mastra skill name in skills.json — unique across BOTH files, so one name
      never means two things
  (b) `repo` is an `https://github.com/<owner>/<repo>` URL — no other host, no
      credentials, no query or fragment
  (c) `ref` is a plain branch or tag name
  (d) the ref actually resolves on the remote (`git ls-remote`), so a typo
      fails here rather than at publish time
  (e) each file is sorted by name
  (f) a template's optional `path`, the folder it lives in when its repo holds
      more than one (a skill and the templates it files), is plain relative
      segments ending in the template's own name

What a container ends up trusting is the RESOLVED commit and per-file sha256
that build_index.py writes into the index, not these files — the link is how
the registry follows an entry, never what a butler executes. That an entry's
`name` equals its own recipe.json `id` / SKILL.md `name` is checked by
scripts/validate.py --all, which clones each entry into a directory named
after it and compares.

Skipped with --offline (no network), which still runs everything but (d).
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
REGISTRY_PATH = REPO_ROOT / "templates.json"
SKILLS_REGISTRY_PATH = REPO_ROOT / "skills.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
# Mastra's skill-name rule: lowercase, digits, single hyphens, none leading or trailing.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_SKILL_NAME = 64
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
REPO_RE = re.compile(r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# One folder name inside a template's repo: no "." or "..", nothing hidden.
PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
MAX_PATH = 200


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


def valid_template_name(name) -> bool:
    return bool(NAME_RE.match(str(name or "")))


def valid_template_path(path, name) -> bool:
    """A template's folder inside its repo: plain relative segments, ending in the
    template's own name, so the checkout validated is still named after the entry."""
    path = str(path)
    parts = path.split("/")
    return len(path) <= MAX_PATH and all(PATH_SEGMENT_RE.match(p) for p in parts) and parts[-1] == name


def valid_skill_name(name) -> bool:
    return bool(SKILL_NAME_RE.match(str(name or ""))) and len(str(name)) <= MAX_SKILL_NAME


def check_listing(
    path: Path, key: str, noun: str, name_ok, offline: bool, owners: dict[str, str],
    allow_path: bool = False,
) -> int:
    """Check one listing file; returns the number of problems. `owners` maps every
    name seen so far to the file that listed it, across both files. `allow_path` lets
    a row name the folder its template lives in (templates.json only)."""
    if not path.exists():
        fail(f"{path.name} not found")
        return 1
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get(key)
    # Empty is allowed, missing is not: an empty list is a legitimate registry (day one, or every entry yanked); a MISSING key is a malformed file.
    # A consumer of an empty index answers "no such template"/"no such skill",
    # which is the same answer it gives for a query nothing matches.
    if not isinstance(rows, list):
        fail(f"{path.name} has no `{key}` list")
        return 1

    problems = 0
    seen: set[str] = set()
    for row in rows:
        name, repo, ref = row.get("name"), row.get("repo"), row.get("ref") or "main"
        if not name_ok(name):
            fail(f"{name!r} is not a valid {noun}"); problems += 1; continue
        if name in seen:
            fail(f"{name}: listed twice"); problems += 1; continue
        seen.add(name)
        if name in owners:
            fail(f"{name}: listed in both {owners[name]} and {path.name} — one name, one kind"); problems += 1; continue
        owners[name] = path.name
        parts = urlsplit(str(repo or ""))
        if not REPO_RE.match(str(repo or "")) or parts.query or parts.fragment or "@" in parts.netloc:
            fail(f"{name}: repo must be https://github.com/<owner>/<repo>, got {repo!r}"); problems += 1; continue
        if not REF_RE.match(str(ref)):
            fail(f"{name}: bad ref {ref!r}"); problems += 1; continue
        sub = row.get("path")
        if sub is not None and not allow_path:
            fail(f"{name}: `path` is for templates.json only; a skill is its repo's root"); problems += 1; continue
        if sub is not None and not valid_template_path(sub, name):
            fail(f"{name}: path must be a folder in the repo ending in {name!r} (templates/{name}), got {sub!r}")
            problems += 1; continue
        if not offline and not ref_exists(repo, ref):
            fail(f"{name}: {repo} has no ref {ref!r}"); problems += 1; continue
        print(f"OK    {name} = {repo} @ {ref}" + (f" : {sub}" if sub is not None else ""))

    if sorted(seen) != [r.get("name") for r in rows]:
        fail(f"{path.name} is not sorted by name"); problems += 1
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Check templates.json and skills.json")
    ap.add_argument("--offline", action="store_true", help="skip the remote ref check")
    args = ap.parse_args()

    owners: dict[str, str] = {}
    problems = check_listing(
        REGISTRY_PATH, "templates", "template id", valid_template_name, args.offline, owners, allow_path=True
    )
    problems += check_listing(SKILLS_REGISTRY_PATH, "skills", "skill name", valid_skill_name, args.offline, owners)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
