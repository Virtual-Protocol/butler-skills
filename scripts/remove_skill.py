#!/usr/bin/env python3
"""remove_skill.py <name> — drop a skill from the registry.

Usage:
    scripts/remove_skill.py my-skill [--yank] [--version X.Y.Z] [--dry-run]

Registering a skill is one PR (scripts/new_skill.py prints it) and a new
version needs no PR — every build re-resolves each skills.json ref. Removal
is the other end of that: the second and last PR a skill ever gets here.

There are two removals, and they differ in what happens to butlers that
ALREADY have the skill installed:

  de-list (default)
      Deletes the skills.json entry. The skill leaves the index, so nothing
      installs it again. Copies already on a butler keep working AND STAY
      ENABLED — the hub client's sync loop iterates index["skills"], so a
      name absent from the index is never visited at all. Use when the skill
      is merely retired and existing users may keep it.

  --yank
      Deletes the skills.json entry AND lists "<name>@<version>" in
      yanked.json, which makes build_index.py publish a yanked:true tombstone
      (no files[], no source). That tombstone is the ONLY thing that disables
      an installed copy: the container marks its lock row yanked, notifies the
      owner once, and refuses to install it again. Use when the skill is
      broken or unsafe.

--yank is one-way. Once a tombstone is dropped again, the sync loop stops
visiting the name, and any lock row already marked keeps yanked:true forever
(the un-yank branch only fires on a live entry). The owner's own
`bevo-hub remove <name>` is the only clear after that.

Writes skills.json (and yanked.json with --yank), then prints the commit and
PR commands. Nothing else in this repo describes a skill, so that is the whole
edit: the index is rebuilt from skills.json on the next publish.

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
REGISTRY_PATH = REPO_ROOT / "skills.json"
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


def published_version(name: str, url: str = INDEX_URL) -> str:
    """The version the live index currently serves for `name`. Only the current
    version is ever published — dist/ is rebuilt from scratch every run — so
    this is the version installed copies are on, and the one worth tombstoning."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            index = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise SystemExit(
            f"could not read the published index ({exc}); pass --version <X.Y.Z> "
            f"with the version to tombstone"
        )
    for entry in index.get("skills") or []:
        if entry.get("name") == name and not entry.get("yanked"):
            version = str(entry.get("version") or "")
            if not SEMVER_RE.match(version):
                raise SystemExit(f"{name}: index version {version!r} is not X.Y.Z; pass --version")
            return version
    raise SystemExit(
        f"{name} has no live entry in {url} — it may never have published, or a "
        f"build has already dropped it. Pass --version <X.Y.Z> to tombstone anyway."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove a skill from the registry.")
    parser.add_argument("name", help="the skill's registry name, e.g. my-dca")
    parser.add_argument(
        "--yank", action="store_true",
        help="also tombstone it (yanked.json) so installed copies are DISABLED, not just de-listed",
    )
    parser.add_argument(
        "--version", help="the version to tombstone (default: whatever the live index serves)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    args = parser.parse_args()

    name = args.name
    if args.version and not args.yank:
        parser.error("--version only means something with --yank")
    if args.version and not SEMVER_RE.match(args.version):
        parser.error(f"--version must be X.Y.Z, got {args.version!r}")

    registry = read_json(REGISTRY_PATH)
    rows = registry.get("skills")
    if not isinstance(rows, list):
        raise SystemExit("skills.json has no `skills` list")
    if not any(row.get("name") == name for row in rows):
        listed = ", ".join(sorted(str(r.get("name")) for r in rows)) or "(none)"
        raise SystemExit(f"{name} is not listed in skills.json. Listed: {listed}")

    registry["skills"] = [row for row in rows if row.get("name") != name]

    yanked_spec = None
    if args.yank:
        version = args.version or published_version(name)
        yanked_spec = f"{name}@{version}"
        yanked = read_json(YANKED_PATH) if YANKED_PATH.exists() else {"yanked": []}
        specs = yanked.get("yanked") or []
        if yanked_spec not in specs:
            specs.append(yanked_spec)
        yanked["yanked"] = sorted(set(specs))

    prefix = "[dry-run] would remove" if args.dry_run else "removed"
    print(f"{prefix} {name} from skills.json ({len(registry['skills'])} skill(s) left)")
    if yanked_spec:
        print(f"{prefix.replace('remove', 'tombstone')} {yanked_spec} in yanked.json")

    if not args.dry_run:
        write_json(REGISTRY_PATH, registry)
        if args.yank:
            write_json(YANKED_PATH, yanked)

    if args.yank:
        effect = (
            "Installed copies will be DISABLED on the next sync and their owners notified once.\n"
            "#    This is one-way: do not drop the tombstone again while any butler may still hold it."
        )
    else:
        effect = (
            "Nothing installs it again, but copies already on a butler KEEP WORKING AND STAY ENABLED.\n"
            "#    Re-run with --yank if they should be disabled instead."
        )

    print(f"""
# {effect}
#
# The second and last PR this skill gets here:
git commit -am "skills: remove {name}"
gh pr create --repo {REGISTRY_REPO} --base main --title "skills: remove {name}"

# Merging to main IS the publish action: the next build drops it from the index
# (hourly cron, or immediately on push to main).""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
