#!/usr/bin/env python3
"""remove_skill.py <name> — drop a duty template from the registry.

Usage:
    scripts/remove_skill.py my-dca [--yank] [--version N] [--dry-run]

Registering a template is one PR (scripts/new_skill.py prints it) and a new
version needs no PR — every build re-resolves each templates.json ref.
Removal is the other end of that: the second and last PR a template ever
gets here.

There are two removals, and they differ in what happens to duties already
created from the template:

  de-list (default)
      Deletes the templates.json entry. The template leaves the index, so
      nothing installs it again. A duty already created from it KEEPS
      RUNNING — the row's `code` is the template's Python, copied in at
      create time, so it needs nothing from this registry afterwards. Use
      when the template is merely retired.

  --yank
      Deletes the templates.json entry AND lists "<name>@<version>" in
      yanked.json, which makes build_index.py publish a tombstone entry (no
      files[], no source) so a fresh `duty_create` against that ref fails
      loudly instead of installing broken or unsafe code. It does not touch
      any duty already running — the container does not re-fetch a
      template after create.

Writes templates.json (and yanked.json with --yank), then prints the commit
and PR commands. Nothing else in this repo describes a template, so that is
the whole edit: the index is rebuilt from templates.json on the next
publish.

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
YANKED_PATH = REPO_ROOT / "yanked.json"

REGISTRY_REPO = "Virtual-Protocol/butler-skills"
INDEX_URL = "https://virtual-protocol.github.io/butler-skills/index.json"


def read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    """2-space indent + trailing newline — the shape both files are already in."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def published_version(name: str, url: str = INDEX_URL) -> int:
    """The version the live index currently serves for `name`. Only the
    current version is ever published — dist/ is rebuilt from scratch every
    run — so this is the version any new duty would be created from, and the
    one worth tombstoning."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            index = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise SystemExit(
            f"could not read the published index ({exc}); pass --version <N> "
            f"with the version to tombstone"
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
    parser = argparse.ArgumentParser(description="Remove a duty template from the registry.")
    parser.add_argument("name", help="the template's registry name, e.g. my-dca")
    parser.add_argument(
        "--yank", action="store_true",
        help="also tombstone it (yanked.json) so a fresh duty_create against that ref fails loudly",
    )
    parser.add_argument(
        "--version", type=int, help="the version to tombstone (default: whatever the live index serves)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    args = parser.parse_args()

    name = args.name
    if args.version is not None and not args.yank:
        parser.error("--version only means something with --yank")

    registry = read_json(REGISTRY_PATH)
    rows = registry.get("templates")
    if not isinstance(rows, list):
        raise SystemExit("templates.json has no `templates` list")
    if not any(row.get("name") == name for row in rows):
        listed = ", ".join(sorted(str(r.get("name")) for r in rows)) or "(none)"
        raise SystemExit(f"{name} is not listed in templates.json. Listed: {listed}")

    registry["templates"] = [row for row in rows if row.get("name") != name]

    yanked_spec = None
    if args.yank:
        version = args.version if args.version is not None else published_version(name)
        yanked_spec = f"{name}@{version}"
        yanked = read_json(YANKED_PATH) if YANKED_PATH.exists() else {"yanked": []}
        specs = yanked.get("yanked") or []
        if yanked_spec not in specs:
            specs.append(yanked_spec)
        yanked["yanked"] = sorted(set(specs))

    prefix = "[dry-run] would remove" if args.dry_run else "removed"
    print(f"{prefix} {name} from templates.json ({len(registry['templates'])} template(s) left)")
    if yanked_spec:
        print(f"{prefix.replace('remove', 'tombstone')} {yanked_spec} in yanked.json")

    if not args.dry_run:
        write_json(REGISTRY_PATH, registry)
        if args.yank:
            write_json(YANKED_PATH, yanked)

    if args.yank:
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
# The second and last PR this template gets here:
git commit -am "templates: remove {name}"
gh pr create --repo {REGISTRY_REPO} --base main --title "templates: remove {name}"

# Merging to main IS the publish action: the next build drops it from the index
# (hourly cron, or immediately on push to main).""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
