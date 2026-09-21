#!/usr/bin/env python3
"""build_index.py — build dist/index.json plus immutable per-version template files.

ONE channel. The registry publishes a single index, the way npm serves one
registry: `dist/templates/<name>/<version>/` is the immutable store, and
`dist/index.json` is the moving pointer at the current version of each
template. A duty pins the version it was created from (`env.RECIPE`); nothing
here auto-updates an existing duty.

Usage:
    scripts/build_index.py [--dry-run]

Reads templates.json — the registry, one {name, repo, ref} entry per
template — shallow-clones every entry at its ref into a throwaway directory,
cross-references yanked.json, and writes:
    dist/index.json
    dist/templates/<name>/<version>/{recipe.json,duty.py,README.md}
Nothing about a template's content is stored in this repository; the clones
are deleted when the build ends. The index is the only published description
of a template — there is no checked-in mirror of it to drift out of date.

Every index entry carries a `source` block naming the template's own git
repository, the ref the registry follows, and the commit that ref resolved to
for THIS build:
    "source": {"repo": "Virtual-Protocol/butler-skill-dca", "ref": "main", "commit": "<40-hex>"}
The commit, not the ref, is what pins the entry: with a branch ref the commit
moves whenever the template repo merges, and the next build republishes it.

A yanked "name@version" (yanked.json) whose template is no longer listed in
templates.json is still published, as a tombstone entry: no files[], no
source, so an install attempt against it fails loudly rather than serving
stale bytes. A yanked version of a template that is still listed (at any
version) gets no tombstone: the live entry is what supersedes it.

`aliases` flattens every template's `recipe.json["supersedes"]` into
`{ref, supersededBy}` rows, so a duty whose `env.RECIPE` names an older
version can still resolve — the container never installs a superseded ref
without checking it first.

Refuses to overwrite an existing dist/templates/<name>/<version>/ directory
unless its contents are byte-identical to what would be written (immutable
publishing). --dry-run performs every check and prints what would be written
without touching disk; it still clones, because the resolved commit and the
per-file hashes can only come from a real checkout.

Python 3.11 stdlib only (plus the `git` binary for the clones).
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "templates.json"
YANKED_PATH = REPO_ROOT / "yanked.json"

PUBLISHED_FILES = ("recipe.json", "duty.py", "README.md")

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
YANKED_SPEC_RE = re.compile(r"^([a-z0-9][a-z0-9-]{1,63})@(\d+)$")
TOMBSTONE_DESCRIPTION = "Withdrawn by its maintainers (yanked) and removed from the registry; not installable."

SCHEMA_VERSION = 3


def load_registry(path: Path | None = None) -> list[dict]:
    """The registry as written: the `templates` list of templates.json, in
    file order. Returned verbatim — the listing is checked by
    scripts/check_registry.py, not filtered or reordered here."""
    path = REGISTRY_PATH if path is None else path
    data = json.loads(path.read_text())
    rows = data.get("templates")
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"{path} has no `templates` list — the registry is empty")
    return rows


def clone_skill(entry: dict, dest: Path) -> Path:
    """Shallow-clone one registry entry at its ref into `dest`.

    The ref may be a branch or a tag; `--depth 1 --branch <ref>` resolves
    either. What the build pins afterwards is the commit this produced, not
    the ref, so a branch that moves changes the index on the next build."""
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
    if not (dest / "recipe.json").exists():
        raise SystemExit(f"{repo} at {ref!r} has no recipe.json at its root")
    return dest


def fetch_skills(work_dir: Path, registry: list[dict] | None = None) -> list[tuple[dict, Path]]:
    """Clone every registry entry under `work_dir`, one directory per
    template. The checkout is named after the registry entry, so the
    directory a template is validated and indexed under is the name the
    registry lists it as."""
    registry = load_registry() if registry is None else registry
    out: list[tuple[dict, Path]] = []
    for entry in registry:
        out.append((entry, clone_skill(entry, work_dir / entry["name"])))
    return out


def resolved_commit(template_dir: Path) -> str:
    """The commit the ref resolved to for this build: HEAD of the clone."""
    try:
        out = subprocess.run(
            ["git", "-C", str(template_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"cannot read the resolved commit of {template_dir}: {e}")
    commit = out.stdout.strip()
    if not COMMIT_RE.match(commit):
        raise SystemExit(f"unexpected `git rev-parse HEAD` output for {template_dir}: {commit!r}")
    return commit


def _short_repo(repo: str) -> str:
    """"https://github.com/Virtual-Protocol/butler-skill-dca" -> "Virtual-Protocol/butler-skill-dca"."""
    parts = urlsplit(repo)
    return parts.path.strip("/")


def source_block(template_dir: Path, entry: dict) -> dict:
    """The `source` block: the repo and ref the registry records for this
    template, plus the commit that ref resolved to in the checkout this build
    cloned. The ref may be a branch, so the commit is the only part of this
    block that identifies exact bytes."""
    return {
        "repo": _short_repo(entry["repo"]),
        "ref": entry.get("ref") or "main",
        "commit": resolved_commit(template_dir),
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_yanked() -> set[str]:
    if not YANKED_PATH.exists():
        return set()
    data = json.loads(YANKED_PATH.read_text())
    return set(data.get("yanked", []))


def tombstone_entries(yanked: set[str], entries: list[dict]) -> list[dict]:
    """A tombstone for every yanked "name@version" whose template has no live
    entry (not listed in templates.json any more). Sorted by spec so the
    output is stable. Refuses a malformed spec — a typo in yanked.json must
    fail the build, not silently yank nothing."""
    live_names = {e["name"] for e in entries}
    tombstones: list[dict] = []
    for spec in sorted(yanked):
        m = YANKED_SPEC_RE.match(spec)
        if not m:
            raise SystemExit(f"yanked.json: {spec!r} is not <id>@<version>")
        name, version = m.group(1), int(m.group(2))
        if name in live_names:
            continue
        tombstones.append({
            "name": name,
            "version": version,
            "description": TOMBSTONE_DESCRIPTION,
            "keywords": [],
            "triggers": [],
            "keys": [],
            "files": [],
        })
    return tombstones


def collect_template(template_dir: Path, entry: dict) -> dict:
    """One index entry, read out of the checkout cloned for `entry`."""
    recipe = json.loads((template_dir / "recipe.json").read_text())
    name = recipe["id"]
    version = recipe["version"]
    files = []
    for fname in PUBLISHED_FILES:
        f = template_dir / fname
        files.append({"path": fname, "sha256": sha256_file(f), "bytes": f.stat().st_size})
    return {
        "name": name,
        "version": version,
        "description": recipe.get("description", ""),
        "keywords": recipe.get("keywords", []),
        "triggers": recipe.get("triggers", []),
        "keys": recipe.get("keys", []),
        "source": source_block(template_dir, entry),
        "files": files,
    }


def build_aliases(templates: list[dict]) -> list[dict]:
    """Flatten every template's recipe.json `supersedes` list into
    {ref, supersededBy} rows. A duty whose env.RECIPE names an older ref can
    still resolve through this list."""
    aliases: list[dict] = []
    for entry, template_dir in templates:
        recipe = json.loads((template_dir / "recipe.json").read_text())
        supersedes = recipe.get("supersedes") or []
        current_ref = f"{recipe['id']}@{recipe['version']}"
        for old_ref in supersedes:
            aliases.append({"ref": old_ref, "supersededBy": current_ref})
    aliases.sort(key=lambda a: a["ref"])
    return aliases


def write_dist_files(template_dir: Path, name: str, version: int, dist_root: Path, dry_run: bool) -> None:
    version_dir = dist_root / "templates" / name / str(version)
    src_files = [template_dir / f for f in PUBLISHED_FILES]
    if version_dir.exists():
        # immutable: refuse unless identical
        for f in src_files:
            dst = version_dir / f.name
            if not dst.exists() or not filecmp.cmp(f, dst, shallow=False):
                raise SystemExit(
                    f"refusing to overwrite existing published version {name}@{version}: "
                    f"{dst} differs from {f} (bump the version instead)"
                )
        return
    if dry_run:
        return
    version_dir.mkdir(parents=True, exist_ok=True)
    for f in src_files:
        shutil.copy2(f, version_dir / f.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dist/index.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    yanked = load_yanked()
    registry = load_registry()

    seen_refs: dict[str, dict] = {}

    # The clones live only for this build: the registry stores links, not files.
    with tempfile.TemporaryDirectory(prefix="butler-skills-build-") as tmp:
        cloned = fetch_skills(Path(tmp), registry)

        entries = []
        for entry, d in cloned:
            collected = collect_template(d, entry)
            ref = f"{collected['name']}@{collected['version']}"
            if ref in seen_refs:
                raise SystemExit(f"two templates resolve to the same {ref} — {entry['repo']} and {seen_refs[ref]['repo']}")
            seen_refs[ref] = entry
            entries.append(collected)

        tombstones = tombstone_entries(yanked, entries)
        aliases = build_aliases(cloned)

        index = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "templates": entries + tombstones,
            "aliases": aliases,
        }

        dist_root = REPO_ROOT / "dist"
        for entry, d in cloned:
            recipe = json.loads((d / "recipe.json").read_text())
            write_dist_files(d, recipe["id"], recipe["version"], dist_root, args.dry_run)

    index_path = dist_root / "index.json"
    if args.dry_run:
        print(f"[dry-run] would write {index_path}")
        print(json.dumps(index, indent=2)[:2000])
    else:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2) + "\n")
        print(f"wrote {index_path}")

    summary = f"{len(entries)} template(s) indexed"
    if tombstones:
        summary += f", plus {len(tombstones)} yanked tombstone(s)"
    if aliases:
        summary += f", {len(aliases)} alias(es)"
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
