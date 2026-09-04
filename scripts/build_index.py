#!/usr/bin/env python3
"""build_index.py — build dist/<channel>/index.json plus immutable per-version
skill files, and regenerate CATALOG.md.

Usage:
    scripts/build_index.py --channel stable|canary [--dry-run] [--repo-tag <tag>]

Reads every skills/<name>/ submodule declared in .gitmodules (they must be
initialised: `git submodule update --init --recursive`), cross-references
yanked.json, writes:
    dist/<channel>/index.json
    dist/skills/<name>/<version>/{SKILL.md,duty.py,CHANGELOG.md}
and regenerates CATALOG.md at the repo root.

Every index entry carries, next to the Pages-served files[] + sha256, an
additive "source" block naming the skill's git repository and the exact commit
the registry pins:
    "source": {"repo": "https://github.com/...", "commit": "<40-hex>", "ref": "v<version>"}
`schemaVersion` stays 1 — the deployed container client rejects any other
value and ignores keys it does not know, so a v1 client keeps installing from
files[] while a git-aware one clones source.commit and falls back to files[].

A yanked "name@version" (yanked.json) whose skill no longer has a submodule is
still published, as a tombstone entry: yanked:true, no files[], no source. The
container's hub client only disables a skill on an index entry that carries
yanked:true — a skill that merely disappears from the index stays installed and
enabled — and it never installs a yanked entry, so the tombstone needs nothing
else. A yanked version of a skill that is still pinned (at any version) gets no
tombstone: the live entry is what un-yanks and updates the container.

Refuses to overwrite an existing dist/skills/<name>/<version>/ directory
unless its contents are byte-identical to what would be written (immutable
publishing). --dry-run performs every check and prints what would be
written without touching disk or the network (the pinned commit is read from
the local submodule checkout with `git rev-parse HEAD`).

Python 3.11 stdlib only (plus the `git` binary for the pinned commit).
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
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
YANKED_PATH = REPO_ROOT / "yanked.json"
CATALOG_PATH = REPO_ROOT / "CATALOG.md"
GITMODULES_PATH = REPO_ROOT / ".gitmodules"

BASE_URL_TEMPLATE = "https://virtual-protocol.github.io/butler-skills"

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
YANKED_SPEC_RE = re.compile(r"^([a-z0-9][a-z0-9-]{1,63})@(\d+\.\d+\.\d+)$")
TOMBSTONE_DESCRIPTION = "Withdrawn by its maintainers (yanked) and removed from the registry; not installable."


def parse_gitmodules(path: Path = GITMODULES_PATH) -> dict[str, dict]:
    """Minimal .gitmodules reader: {path: {"name": ..., "url": ...}}. Same
    shape as validate.py's copy; kept independent on purpose (see below)."""
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


def list_skill_dirs(repo_root: Path = REPO_ROOT) -> list[Path]:
    """The skills/<name> submodules declared in .gitmodules, sorted by path.
    Fails loudly on an uninitialised one — an index built from a registry
    with a missing checkout would silently drop a published skill."""
    entries = parse_gitmodules(repo_root / ".gitmodules")
    dirs = []
    for rel in sorted(entries):
        if not rel.startswith("skills/"):
            continue
        d = repo_root / rel
        if not (d / "SKILL.md").exists():
            raise SystemExit(f"{rel} is declared in .gitmodules but has no SKILL.md — run `git submodule update --init --recursive`")
        dirs.append(d)
    return dirs


def pinned_commit(skill_dir: Path) -> str:
    """The commit the registry pins for this submodule: HEAD of its checkout.
    Local only — no fetch, so --dry-run works offline."""
    try:
        out = subprocess.run(
            ["git", "-C", str(skill_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"cannot read the pinned commit of {skill_dir}: {e}")
    commit = out.stdout.strip()
    if not COMMIT_RE.match(commit):
        raise SystemExit(f"unexpected `git rev-parse HEAD` output for {skill_dir}: {commit!r}")
    return commit


def source_block(skill_dir: Path, version: str, gitmodules: dict[str, dict] | None = None) -> dict:
    """The additive per-entry "source" block: the skill repo, the pinned
    commit and the tag the registry expects that commit to carry (v<version>;
    CI asserts the two agree, see scripts/check_pins.py)."""
    gitmodules = parse_gitmodules() if gitmodules is None else gitmodules
    rel = skill_dir.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    entry = gitmodules.get(rel)
    if entry is None or not entry.get("url"):
        raise SystemExit(f"{rel} has no url in .gitmodules — registry skills are git submodules")
    return {"repo": entry["url"], "commit": pinned_commit(skill_dir), "ref": f"v{version}"}


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
    no live entry (no submodule any more). Sorted by spec so the output is
    stable. Refuses a malformed spec — a typo in yanked.json must fail the
    build, not silently yank nothing."""
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


def collect_skill(skill_dir: Path, yanked: set[str], gitmodules: dict[str, dict] | None = None) -> dict:
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text()
    fm = parse_frontmatter_light(text)
    butler = fm.get("metadata", {}).get("butler", {})
    name = fm["name"]
    version = fm["version"]
    files = []
    for f in sorted(skill_dir.iterdir()):
        if f.is_file() and f.name in ("SKILL.md", "duty.py", "CHANGELOG.md"):
            files.append({"path": f.name, "sha256": sha256_file(f), "bytes": f.stat().st_size})
    entry = {
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
        "source": source_block(skill_dir, version, gitmodules),
    }
    return entry


def repo_slug(url: str) -> str:
    """'https://github.com/owner/repo(.git)' -> 'owner/repo' for the catalog."""
    slug = url.rstrip("/")
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    return slug[len("https://github.com/"):] if slug.startswith("https://github.com/") else slug


def write_dist_files(skill_dir: Path, name: str, version: str, dist_root: Path, dry_run: bool) -> None:
    version_dir = dist_root / "skills" / name / version
    src_files = [f for f in skill_dir.iterdir() if f.is_file() and f.name in ("SKILL.md", "duty.py", "CHANGELOG.md")]
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
        "Each skill is its own git repository, pinned here as a submodule at the commit of its",
        "`v<version>` tag (the Repo link opens that tag). Butler clones exactly that commit.",
        "",
        "| Skill | Version | Repo | Tier | Modes | Money-moving | Description |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in sorted(entries, key=lambda x: x["name"]):
        yank_marker = " (yanked)" if e["yanked"] else ""
        src = e.get("source") or {}
        repo_cell = "—"
        if src.get("repo"):
            repo_url = src["repo"].rstrip("/")
            if repo_url.endswith(".git"):
                repo_url = repo_url[: -len(".git")]
            repo_cell = f"[{repo_slug(src['repo'])}]({repo_url}/tree/{src.get('ref', '')})"
        lines.append(
            f"| `{e['name']}`{yank_marker} | {e['version']} | {repo_cell} | {e['tier']} | {', '.join(e['modes'])} | "
            f"{'yes' if e['moneyMoving'] else 'no'} | {e['description']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dist/<channel>/index.json and CATALOG.md")
    parser.add_argument("--channel", choices=["stable", "canary"], default="canary")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo-tag", default="dev")
    args = parser.parse_args()

    yanked = load_yanked()
    gitmodules = parse_gitmodules()
    skill_dirs = list_skill_dirs()

    entries = []
    for d in skill_dirs:
        entries.append(collect_skill(d, yanked, gitmodules))
    tombstones = tombstone_entries(yanked, entries)
    entries.extend(tombstones)

    index = {
        "schemaVersion": 1,
        "channel": args.channel,
        "repoTag": args.repo_tag,
        "builtAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseUrl": BASE_URL_TEMPLATE,
        "signature": None,
        "skills": entries,
    }

    dist_root = REPO_ROOT / "dist"
    for d in skill_dirs:
        fm = parse_frontmatter_light((d / "SKILL.md").read_text())
        write_dist_files(d, fm["name"], fm["version"], dist_root, args.dry_run)

    index_path = dist_root / args.channel / "index.json"
    if args.dry_run:
        print(f"[dry-run] would write {index_path}")
        print(json.dumps(index, indent=2)[:2000])
    else:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2) + "\n")
        print(f"wrote {index_path}")

    catalog = regenerate_catalog(entries)
    if args.dry_run:
        print(f"[dry-run] would write {CATALOG_PATH} ({len(catalog)} chars)")
    else:
        CATALOG_PATH.write_text(catalog)
        print(f"wrote {CATALOG_PATH}")

    summary = f"{len(entries) - len(tombstones)} skill(s) indexed for channel={args.channel}"
    if tombstones:
        summary += f", plus {len(tombstones)} yanked tombstone(s)"
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
