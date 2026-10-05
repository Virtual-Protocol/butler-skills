#!/usr/bin/env python3
"""remove_skill.py <name> — drop a duty template or a skill from the registry.

Usage:
    scripts/remove_skill.py my-dca [--yank] [--version N] [--dry-run]         # a template
    scripts/remove_skill.py my-skill [--yank] [--version X.Y.Z] [--dry-run]   # a skill

The name says which: it is listed in templates.json or in skills.json, never
both. Registering one is one PR (scripts/new_skill.py prints it) and a new
version needs no PR — every build re-resolves each ref. Removal is the other
end of that: the second and last PR an entry ever gets here.

There are two removals.

  de-list (default)
      Deletes the listing entry, so the next build drops it from the index
      and nothing installs it again.
      - A template: a duty already created from it KEEPS RUNNING — the row's
        `code` is the template's Python, copied in at create time, so it needs
        nothing from this registry afterwards.
      - A skill: a butler that installed it removes it on its next registry
        sync (within ~15 minutes), because the index no longer lists it.

  --yank
      Also lists "<name>@<version>" in yanked.json, which makes build_index.py
      publish a tombstone for that version: `{name, version, yanked: true,
      files: []}` for a skill, a files-less entry for a template.
      - A template: a fresh `duty_create` against that ref fails loudly
        instead of installing broken or unsafe code. It does not touch any
        duty already running — the container never re-fetches a template.
      - A skill: the tombstone says that exact version is withdrawn, so no
        butler installs it again and one holding it drops it on its next sync.

Writes templates.json or skills.json (and yanked.json with --yank), then
prints the commit and PR commands. Nothing else in this repo describes an
entry, so that is the whole edit: the index is rebuilt from the listings on
the next publish.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "templates.json"
SKILLS_REGISTRY_PATH = REPO_ROOT / "skills.json"
YANKED_PATH = REPO_ROOT / "yanked.json"

REGISTRY_REPO = "Virtual-Protocol/butler-skills"
INDEX_URL = "https://virtual-protocol.github.io/butler-skills/index.json"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    """2-space indent + trailing newline — the shape both files are already in."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def published_version(name: str, url: str = INDEX_URL, kind: str = "template"):
    """The version the live index currently serves for `name` — an integer for a
    template, "X.Y.Z" for a skill. Only the current version is ever published
    (dist/ is rebuilt from scratch every run), so this is the version any new
    install would get, and the one worth tombstoning."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            index = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        example = "X.Y.Z" if kind == "skill" else "N"
        raise SystemExit(
            f"could not read the published index ({exc}); pass --version <{example}> "
            f"with the version to tombstone"
        )
    if kind == "skill":
        for entry in index.get("skills") or []:
            if entry.get("name") == name and entry.get("files") and not entry.get("yanked"):
                version = entry.get("version")
                if not isinstance(version, str) or not SEMVER_RE.match(version):
                    raise SystemExit(f"{name}: index version {version!r} is not X.Y.Z; pass --version")
                return version
        raise SystemExit(
            f"{name} has no live entry in {url} — it may never have published, or a "
            f"build has already dropped it. Pass --version <X.Y.Z> to tombstone anyway."
        )
    for entry in index.get("templates") or []:
        if entry.get("name") == name and entry.get("files"):  # a tombstone has no files[]
            version = entry.get("version")
            if not isinstance(version, int):
                raise SystemExit(f"{name}: index version {version!r} is not an integer; pass --version")
            return version
    raise SystemExit(
        f"{name} has no live entry in {url} — it may never have published, or a "
        f"build has already dropped it. Pass --version <N> to tombstone anyway."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove a duty template or a skill from the registry.")
    parser.add_argument("name", help="the registry name, e.g. my-dca")
    parser.add_argument(
        "--yank", action="store_true",
        help="also tombstone that version (yanked.json) — see the module docstring for what that does per kind",
    )
    parser.add_argument(
        "--version",
        help="the version to tombstone: an integer for a template, X.Y.Z for a skill "
        "(default: whatever the live index serves)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    args = parser.parse_args()

    name = args.name
    if args.version is not None and not args.yank:
        parser.error("--version only means something with --yank")

    registry = read_json(REGISTRY_PATH)
    template_rows = registry.get("templates")
    if not isinstance(template_rows, list):
        raise SystemExit("templates.json has no `templates` list")
    skills_registry = read_json(SKILLS_REGISTRY_PATH) if SKILLS_REGISTRY_PATH.exists() else {"skills": []}
    skill_rows = skills_registry.get("skills")
    if not isinstance(skill_rows, list):
        raise SystemExit("skills.json has no `skills` list")

    in_templates = any(row.get("name") == name for row in template_rows)
    in_skills = any(row.get("name") == name for row in skill_rows)
    if in_templates and in_skills:
        raise SystemExit(f"{name} is listed in both templates.json and skills.json — fix the listing first")
    if not in_templates and not in_skills:
        templates = ", ".join(sorted(str(r.get("name")) for r in template_rows)) or "(none)"
        skills = ", ".join(sorted(str(r.get("name")) for r in skill_rows)) or "(none)"
        raise SystemExit(f"{name} is not listed in templates.json or skills.json. Listed templates: {templates}; skills: {skills}")

    if in_skills:
        kind, listing, path, key, noun = "skill", skills_registry, SKILLS_REGISTRY_PATH, "skills", "skill"
    else:
        kind, listing, path, key, noun = "template", registry, REGISTRY_PATH, "templates", "template"

    version = None
    if args.version is not None:
        if kind == "skill" and not SEMVER_RE.match(args.version):
            parser.error(f"{name} is a skill: --version must be X.Y.Z, got {args.version!r}")
        if kind == "template":
            if not args.version.isdigit() or int(args.version) < 1:
                parser.error(f"{name} is a template: --version must be a positive integer, got {args.version!r}")
            version = int(args.version)
        else:
            version = args.version

    listing[key] = [row for row in listing[key] if row.get("name") != name]

    yanked_spec = None
    if args.yank:
        if version is None:
            version = published_version(name, kind=kind)
        yanked_spec = f"{name}@{version}"
        yanked = read_json(YANKED_PATH) if YANKED_PATH.exists() else {"yanked": []}
        specs = yanked.get("yanked") or []
        if yanked_spec not in specs:
            specs.append(yanked_spec)
        yanked["yanked"] = sorted(set(specs))

    prefix = "[dry-run] would remove" if args.dry_run else "removed"
    print(f"{prefix} {name} from {path.name} ({len(listing[key])} {noun}(s) left)")
    if yanked_spec:
        print(f"{prefix.replace('remove', 'tombstone')} {yanked_spec} in yanked.json")

    if not args.dry_run:
        write_json(path, listing)
        if args.yank:
            write_json(YANKED_PATH, yanked)

    if kind == "skill":
        if args.yank:
            effect = (
                "That version is withdrawn: no butler installs it again, and one holding it drops it\n"
                "#    on its next registry sync (within ~15 minutes)."
            )
        else:
            effect = (
                "Nothing installs it again, and a butler that installed it removes it on its next\n"
                "#    registry sync (within ~15 minutes). Re-run with --yank to tombstone the version too."
            )
    elif args.yank:
        effect = (
            "A fresh duty_create against that ref will now fail loudly instead of installing it.\n"
            "#    Nothing already created from it is touched — the container never re-fetches a template."
        )
    else:
        effect = (
            "Nothing installs it again, but a duty already created from it keeps running.\n"
            "#    Re-run with --yank to tombstone the ref instead."
        )

    print(f"""
# {effect}
#
# The second and last PR this {noun} gets here:
git commit -am "{key}: remove {name}"
gh pr create --repo {REGISTRY_REPO} --base main --title "{key}: remove {name}"

# Merging to main IS the publish action: the next build drops it from the index
# (hourly cron, or immediately on push to main).""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
