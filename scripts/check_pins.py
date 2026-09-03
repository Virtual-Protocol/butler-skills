#!/usr/bin/env python3
"""check_pins.py — CI guard for the git-backed registry (.github/workflows/validate.yml).

Usage:
    scripts/check_pins.py [--root <repo>] [--no-fetch]

For every `skills/<name>` submodule declared in .gitmodules it asserts:

  (a) the pinned commit is the commit of tag `v<version>` in the skill's
      repository, where <version> is the frontmatter `version` of the pinned
      SKILL.md — checked as `git -C skills/<name> tag --points-at HEAD`
      containing `v<version>` (tags are fetched first unless --no-fetch);
  (b) the submodule URL is an `https://github.com/<owner>/<repo>` URL
      (never ssh, never another host, never a local path);
  (c) the pinned checkout holds no symlinks and no nested submodules or
      repositories (no `.gitmodules`, no `.git` below the top level);
  (d) the submodule is initialised and its path is exactly `skills/<frontmatter name>`.

Exit 1 on the first failing skill (all skills are still reported). The same
tree rules run inside scripts/validate.py; this script is the one place the
tag rule lives, because it needs the skill repo's tags, i.e. the network.

Python 3.11 stdlib + git.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SUBMODULE_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
TOP_LEVEL_SKIP = {".git", "__pycache__"}


def parse_gitmodules(path: Path) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    if not path.exists():
        return entries
    current: dict | None = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        m = re.match(r'^\[submodule\s+"(.+)"\]$', line)
        if m:
            current = {"name": m.group(1)}
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*=\s*(.*)$", line)
        if m and current is not None:
            current[m.group(1)] = m.group(2).strip()
            if m.group(1) == "path":
                entries[current["path"]] = current
    return entries


def frontmatter_field(skill_md: Path, key: str) -> str | None:
    lines = skill_md.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(rf"^{re.escape(key)}:\s*(.*)$", line)
        if m:
            return m.group(1).strip()
    return None


def tree_problems(skill_dir: Path) -> list[str]:
    """Symlinks anywhere, a .gitmodules anywhere, a .git below the top level."""
    problems: list[str] = []
    for root, dirs, files in os.walk(skill_dir, followlinks=False):
        root_path = Path(root)
        top = root_path == skill_dir
        keep = []
        for d in dirs:
            p = root_path / d
            rel = p.relative_to(skill_dir).as_posix()
            if top and d in TOP_LEVEL_SKIP:
                continue
            if p.is_symlink():
                problems.append(f"symlink: {rel}")
                continue
            if d == ".git":
                problems.append(f"nested git repository: {rel}")
                continue
            keep.append(d)
        dirs[:] = keep
        for f in files:
            p = root_path / f
            rel = p.relative_to(skill_dir).as_posix()
            if top and f in TOP_LEVEL_SKIP:
                continue
            if p.is_symlink():
                problems.append(f"symlink: {rel}")
            elif f == ".gitmodules":
                problems.append(f"nested submodules: {rel}")
            elif f == ".git":
                problems.append(f"nested git repository: {rel}")
    return problems


def git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {out.stderr.strip()}")
    return out.stdout


def check_skill(rel_path: str, entry: dict, root: Path, fetch: bool) -> list[str]:
    errors: list[str] = []
    skill_dir = root / rel_path
    name = rel_path[len("skills/"):]

    if "/" in name or not NAME_RE.match(name):
        errors.append(f"submodule path {rel_path!r} must be skills/<name> with a valid skill name")

    url = entry.get("url", "")
    if not SUBMODULE_URL_RE.match(url):
        errors.append(f"url {url!r} must be https://github.com/<owner>/<repo>")

    if not (skill_dir / ".git").exists():
        errors.append("not initialised — run `git submodule update --init --recursive`")
        return errors

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        errors.append("SKILL.md missing in the pinned checkout")
        return errors

    fm_name = frontmatter_field(skill_md, "name")
    if fm_name != name:
        errors.append(f"frontmatter name {fm_name!r} must equal the submodule path name {name!r}")

    version = frontmatter_field(skill_md, "version") or ""
    if not SEMVER_RE.match(version):
        errors.append(f"frontmatter version {version!r} is not semver X.Y.Z")

    errors.extend(tree_problems(skill_dir))

    # (a) the pinned commit must carry tag v<version> in the skill repo
    try:
        if fetch:
            git(["fetch", "--tags", "--force", "--quiet", "origin"], skill_dir)
        head = git(["rev-parse", "HEAD"], skill_dir).strip()
        tags = {t.strip() for t in git(["tag", "--points-at", "HEAD"], skill_dir).splitlines() if t.strip()}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        errors.append(str(e))
        return errors
    expected = f"v{version}"
    if version and expected not in tags:
        errors.append(
            f"pinned commit {head} does not carry tag {expected} "
            f"(tags at HEAD: {sorted(tags) or 'none'}) — tag that commit in the skill repo, or move the pointer"
        )
    return errors


def run(root: Path, fetch: bool) -> int:
    entries = parse_gitmodules(root / ".gitmodules")
    skill_entries = {p: e for p, e in entries.items() if p.startswith("skills/")}
    stray = [p for p in entries if not p.startswith("skills/")]
    all_ok = True
    for p in stray:
        print(f"ERROR {p}: submodules are only allowed under skills/")
        all_ok = False
    skills_dir = root / "skills"
    if skills_dir.exists():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name != "__pycache__" and f"skills/{d.name}" not in skill_entries:
                print(f"ERROR skills/{d.name}: not a submodule (registry skills are git-backed; vendored directories are refused)")
                all_ok = False
    if not skill_entries:
        print("no skills/<name> submodules declared in .gitmodules")
    for rel_path, entry in sorted(skill_entries.items()):
        errors = check_skill(rel_path, entry, root, fetch)
        if errors:
            all_ok = False
            for e in errors:
                print(f"ERROR {rel_path}: {e}")
        else:
            head = git(["rev-parse", "HEAD"], root / rel_path).strip()
            version = frontmatter_field(root / rel_path / "SKILL.md", "version")
            print(f"OK    {rel_path} = {entry.get('url')} @ {head} (v{version})")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assert every skills/<name> submodule is pinned at its v<version> tag.")
    parser.add_argument("--root", default=str(REPO_ROOT), help="registry checkout to check (default: this repo)")
    parser.add_argument("--no-fetch", action="store_true", help="do not fetch tags first (offline; the local checkout must already have them)")
    args = parser.parse_args(argv)
    return run(Path(args.root).resolve(), fetch=not args.no_fetch)


if __name__ == "__main__":
    sys.exit(main())
