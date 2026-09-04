#!/usr/bin/env python3
"""build_index.py — build dist/index.json plus immutable per-version skill
files, and regenerate CATALOG.md.

ONE channel. The registry publishes a single index, the way npm serves one
registry: `dist/skills/<name>/<version>/` is the immutable store, and
`dist/index.json` is the moving pointer at the current version of each skill.
An environment does not get its own view — a container that wants to hold a
version pins it (`bevo-hub install <name>@<version>`, which never auto-updates).

Usage:
    scripts/build_index.py [--dry-run] [--repo-tag <tag>]

Reads skills.json — the registry, one {name, repo, ref} entry per skill —
shallow-clones every entry at its ref into a throwaway directory, cross-
references yanked.json, and writes:
    dist/index.json
    dist/skills/<name>/<version>/{SKILL.md,duty.py,CHANGELOG.md}
and regenerates CATALOG.md at the repo root. Nothing about a skill's content
is stored in this repository; the clones are deleted when the build ends.

Every index entry carries, next to the Pages-served files[] + sha256, an
additive "source" block naming the skill's git repository, the ref the registry
follows, and the commit that ref resolved to for THIS build:
    "source": {"repo": "https://github.com/...", "commit": "<40-hex>", "ref": "main"}
The commit, not the ref, is what pins the entry: with a branch ref the commit
moves whenever the skill repo merges, and the next build republishes it.
`schemaVersion` stays 1 — the deployed container client rejects any other
value and ignores keys it does not know, so a v1 client keeps installing from
files[] while a git-aware one clones source.commit and falls back to files[].

A yanked "name@version" (yanked.json) whose skill is no longer listed in
skills.json is still published, as a tombstone entry: yanked:true, no files[],
no source. The container's hub client only disables a skill on an index entry
that carries yanked:true — a skill that merely disappears from the index stays
installed and enabled — and it never installs a yanked entry, so the tombstone
needs nothing else. A yanked version of a skill that is still listed (at any
version) gets no tombstone: the live entry is what un-yanks and updates the
container.

Refuses to overwrite an existing dist/skills/<name>/<version>/ directory
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

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "skills.json"
YANKED_PATH = REPO_ROOT / "yanked.json"
CATALOG_PATH = REPO_ROOT / "CATALOG.md"

BASE_URL_TEMPLATE = "https://virtual-protocol.github.io/butler-skills"

PUBLISHED_FILES = ("SKILL.md", "duty.py", "CHANGELOG.md")

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
YANKED_SPEC_RE = re.compile(r"^([a-z0-9][a-z0-9-]{1,63})@(\d+\.\d+\.\d+)$")
TOMBSTONE_DESCRIPTION = "Withdrawn by its maintainers (yanked) and removed from the registry; not installable."


def load_registry(path: Path = REGISTRY_PATH) -> list[dict]:
    """The registry as written: the `skills` list of skills.json, in file
    order. Returned verbatim — the listing is checked by
    scripts/check_registry.py, not filtered or reordered here."""
    data = json.loads(path.read_text())
    rows = data.get("skills")
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"{path} has no `skills` list — the registry is empty")
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
    if not (dest / "SKILL.md").exists():
        raise SystemExit(f"{repo} at {ref!r} has no SKILL.md at its root")
    return dest


def fetch_skills(work_dir: Path, registry: list[dict] | None = None) -> list[tuple[dict, Path]]:
    """Clone every registry entry under `work_dir`, one directory per skill.

    The checkout is named after the registry entry, so the directory a skill is
    validated and indexed under is the name the registry lists it as."""
    registry = load_registry() if registry is None else registry
    out: list[tuple[dict, Path]] = []
    for entry in registry:
        out.append((entry, clone_skill(entry, work_dir / entry["name"])))
    return out


def resolved_commit(skill_dir: Path) -> str:
    """The commit the ref resolved to for this build: HEAD of the clone."""
    try:
        out = subprocess.run(
            ["git", "-C", str(skill_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"cannot read the resolved commit of {skill_dir}: {e}")
    commit = out.stdout.strip()
    if not COMMIT_RE.match(commit):
        raise SystemExit(f"unexpected `git rev-parse HEAD` output for {skill_dir}: {commit!r}")
    return commit


def source_block(skill_dir: Path, entry: dict) -> dict:
    """The additive per-entry "source" block: the repo and ref the registry
    records for this skill, plus the commit that ref resolved to in the
    checkout this build cloned. The ref may be a branch, so the commit is the
    only part of this block that identifies exact bytes."""
    return {
        "repo": entry["repo"],
        "commit": resolved_commit(skill_dir),
        "ref": entry.get("ref") or "main",
    }


def parse_frontmatter_light(text: str) -> dict:
    """Minimal frontmatter reader shared in spirit with validate.py's parser,
    kept intentionally independent (build_index must never depend on the
    validator's internal issue-reporting state)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter fence")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("missing closing frontmatter fence")
    fm: dict = {}
    i = 1
    while i < end:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2)
            if key == "metadata":
                fm["metadata"] = json.loads(value)
            else:
                fm[key] = value.strip()
        i += 1
    fm["_body"] = "\n".join(lines[end + 1:])
    return fm


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
    """A yanked:true tombstone for every yanked "name@version" whose skill has
    no live entry (not listed in skills.json any more). Sorted by spec so the
    output is stable. Refuses a malformed spec — a typo in yanked.json must
    fail the build, not silently yank nothing."""
    live_names = {e["name"] for e in entries}
    tombstones: list[dict] = []
    for spec in sorted(yanked):
        m = YANKED_SPEC_RE.match(spec)
        if not m:
            raise SystemExit(f"yanked.json: {spec!r} is not <name>@<X.Y.Z>")
        name, version = m.group(1), m.group(2)
        if name in live_names:
            continue
        tombstones.append({
            "name": name,
            "version": version,
            "description": TOMBSTONE_DESCRIPTION,
            "tier": "on-demand",
            "modes": [],
            "moneyMoving": False,
            "keywords": [],
            "params": [],
            "requires": {},
            "yanked": True,
            "files": [],
        })
    return tombstones


def render_contracts_section(web3: dict) -> str:
    if not web3 or not web3.get("contracts"):
        return ""
    rows = ["| Contract | Chain | Function | Selector |", "| --- | --- | --- | --- |"]
    for c in web3.get("contracts", []):
        for fn in c.get("functions", []):
            rows.append(f"| {c.get('name')} ({c.get('address')}) | {c.get('chainId')} | `{fn.get('signature')}` | `{fn.get('selector')}` |")
    return "\n".join(rows)


def collect_skill(skill_dir: Path, yanked: set[str], entry: dict) -> dict:
    """One index entry, read out of the checkout cloned for `entry`."""
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text()
    fm = parse_frontmatter_light(text)
    butler = fm.get("metadata", {}).get("butler", {})
    name = fm["name"]
    version = fm["version"]
    files = []
    for f in sorted(skill_dir.iterdir()):
        if f.is_file() and f.name in PUBLISHED_FILES:
            files.append({"path": f.name, "sha256": sha256_file(f), "bytes": f.stat().st_size})
    return {
        "name": name,
        "version": version,
        "description": fm.get("description", ""),
        "tier": butler.get("tier", "on-demand"),
        "modes": butler.get("modes", []),
        "moneyMoving": bool(butler.get("moneyMoving", False)),
        "keywords": butler.get("keywords", []),
        "params": butler.get("params", []),
        "requires": butler.get("requires", {}),
        "yanked": f"{name}@{version}" in yanked,
        "files": files,
        "source": source_block(skill_dir, entry),
    }


def repo_slug(url: str) -> str:
    """'https://github.com/owner/repo(.git)' -> 'owner/repo' for the catalog."""
    slug = url.rstrip("/")
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    return slug[len("https://github.com/"):] if slug.startswith("https://github.com/") else slug


def write_dist_files(skill_dir: Path, name: str, version: str, dist_root: Path, dry_run: bool) -> None:
    version_dir = dist_root / "skills" / name / version
    src_files = [f for f in skill_dir.iterdir() if f.is_file() and f.name in PUBLISHED_FILES]
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


def regenerate_catalog(entries: list[dict]) -> str:
    lines = [
        "# Catalog",
        "",
        "Generated by `scripts/build_index.py`. Do not edit by hand.",
        "",
        "Each skill is its own git repository, listed in `skills.json` as a link plus the ref",
        "the registry follows (the Repo link opens that ref). Every build re-resolves the ref to",
        "a commit and republishes; the index records that commit, and Butler clones exactly it.",
        "",
        "| Skill | Version | Repo | Tier | Modes | Money-moving | Description |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in sorted(entries, key=lambda x: x["name"]):
        yank_marker = " (yanked)" if e.get("yanked") else ""
        src = e.get("source") or {}
        repo_cell = "—"
        if src.get("repo"):
            repo_url = src["repo"].rstrip("/")
            if repo_url.endswith(".git"):
                repo_url = repo_url[: -len(".git")]
            repo_cell = f"[{repo_slug(src['repo'])}]({repo_url}/tree/{src.get('ref', '')})"
        lines.append(
            f"| `{e['name']}`{yank_marker} | {e.get('version', '')} | {repo_cell} | {e.get('tier', '')} | "
            f"{', '.join(e.get('modes', []))} | {'yes' if e.get('moneyMoving') else 'no'} | {e.get('description', '')} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dist/index.json and CATALOG.md")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo-tag", default="dev")
    args = parser.parse_args()

    yanked = load_yanked()
    registry = load_registry()

    # The clones live only for this build: the registry stores links, not files.
    with tempfile.TemporaryDirectory(prefix="butler-skills-build-") as tmp:
        cloned = fetch_skills(Path(tmp), registry)

        entries = [collect_skill(d, yanked, entry) for entry, d in cloned]
        tombstones = tombstone_entries(yanked, entries)

        index = {
            "schemaVersion": 1,
            "repoTag": args.repo_tag,
            "builtAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "baseUrl": BASE_URL_TEMPLATE,
            "signature": None,
            "skills": entries + tombstones,
        }

        dist_root = REPO_ROOT / "dist"
        for entry, d in cloned:
            fm = parse_frontmatter_light((d / "SKILL.md").read_text())
            write_dist_files(d, fm["name"], fm["version"], dist_root, args.dry_run)

    index_path = dist_root / "index.json"
    if args.dry_run:
        print(f"[dry-run] would write {index_path}")
        print(json.dumps(index, indent=2)[:2000])
    else:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2) + "\n")
        print(f"wrote {index_path}")

    catalog = regenerate_catalog(index["skills"])
    if args.dry_run:
        print(f"[dry-run] would write {CATALOG_PATH} ({len(catalog)} chars)")
    else:
        CATALOG_PATH.write_text(catalog)
        print(f"wrote {CATALOG_PATH}")

    summary = f"{len(entries)} skill(s) indexed"
    if tombstones:
        summary += f", plus {len(tombstones)} yanked tombstone(s)"
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
