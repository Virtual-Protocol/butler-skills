#!/usr/bin/env python3
"""build_index.py — build dist/index.json plus the immutable per-version files it points at.

ONE channel. The registry publishes a single index, the way npm serves one
registry: `dist/templates/<name>/<version>/` and `dist/skills/<name>/<version>/`
are the immutable store, and `dist/index.json` is the moving pointer at the
current version of each template and skill. A duty pins the template version
it was created from (`env.RECIPE`); nothing here auto-updates an existing duty.

Usage:
    scripts/build_index.py [--dry-run] [--live-index <url>]

Reads the two listings — templates.json and skills.json, one {name, repo, ref}
entry each — shallow-clones every entry at its ref into a throwaway directory,
cross-references yanked.json, and writes:
    dist/index.json
    dist/templates/<name>/<version>/{recipe.json,duty.py,README.md}
    dist/skills/<name>/<X.Y.Z>/{SKILL.md,references/**/*.md}
Nothing about a template's or skill's content is stored in this repository;
the clones are deleted when the build ends. The index is the only published
description of either — there is no checked-in mirror of it to drift.

The index is `{schemaVersion: 3, generatedAt, templates, aliases, skills}`.
`schemaVersion` stays 3: the container hard-requires it and ignores top-level
keys it does not know, so `skills` is additive for a butler that only reads
`templates`.

Every entry carries a `source` block naming its own git repository, the ref
the registry follows, and the commit that ref resolved to for THIS build:
    "source": {"repo": "Virtual-Protocol/butler-skill-dca", "ref": "main", "commit": "<40-hex>"}
The commit, not the ref, is what pins the entry: with a branch ref the commit
moves whenever the repo merges, and the next build republishes it.

Templates (unchanged): a yanked "name@N" (yanked.json) whose template is no
longer listed in templates.json is still published, as a tombstone entry: no
files[], no source, so an install attempt against it fails loudly rather than
serving stale bytes. A yanked version of a template that is still listed (at
any version) gets no tombstone: the live entry is what supersedes it.
`aliases` flattens every template's `recipe.json["supersedes"]` into
`{ref, supersededBy}` rows.

Skills: every listed skill is run through scripts/validate.py (maintainer
mode) and ANY error fails the whole build. That is deliberate — a failed
build leaves the last Pages deploy live, where silently skipping a skill that
stopped validating would delist it from every butler at once. Every yanked
"name@X.Y.Z" becomes a tombstone row `{name, version, yanked: true, files: []}`
— whether or not the skill is still listed, so a butler holding that version
can drop it — and a listed skill whose current version is yanked publishes
only the tombstone. A skill's `name@version` is immutable ACROSS builds: the
live index (https://virtual-protocol.github.io/butler-skills/index.json, or
--live-index / $BUTLER_SKILLS_LIVE_INDEX_URL) is fetched, and a version it
already serves with different file hashes, or has yanked, is refused. An
unreachable live index is a warning, not a failure. A skill row carries
`maxSteps` when the skill sets one and always `requires: {bins, skills}`; every
skill a published row requires must be published by the same build (listed,
and not at a yanked version), and the requirements must not form a cycle —
the butler installs a required skill first, so anything else fails the build.

Within one build, an existing dist/<kind>/<name>/<version>/ directory is never
overwritten with different bytes either. --dry-run performs every check and
prints what would be written without touching disk; it still clones, because
the resolved commit and the per-file hashes can only come from a real
checkout.

Python 3.11 stdlib only (plus the `git` binary for the clones).
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = REPO_ROOT / "templates.json"
SKILLS_REGISTRY_PATH = REPO_ROOT / "skills.json"
YANKED_PATH = REPO_ROOT / "yanked.json"

PUBLISHED_FILES = ("recipe.json", "duty.py", "README.md")

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
YANKED_SPEC_RE = re.compile(r"^([a-z0-9][a-z0-9-]{1,63})@(\d+)$")
SKILL_YANKED_SPEC_RE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)@(\d+\.\d+\.\d+)$")
TOMBSTONE_DESCRIPTION = "Withdrawn by its maintainers (yanked) and removed from the registry; not installable."

SCHEMA_VERSION = 3

LIVE_INDEX_URL = "https://virtual-protocol.github.io/butler-skills/index.json"
LIVE_INDEX_ENV = "BUTLER_SKILLS_LIVE_INDEX_URL"


def load_registry(path: Path | None = None) -> list[dict]:
    """The registry as written: the `templates` list of templates.json, in
    file order. Returned verbatim — the listing is checked by
    scripts/check_registry.py, not filtered or reordered here."""
    path = REGISTRY_PATH if path is None else path
    data = json.loads(path.read_text())
    rows = data.get("templates")
    # Empty is allowed, missing is not: an empty list is a legitimate registry (day one, or every template yanked); a MISSING key is a malformed file.
    if not isinstance(rows, list):
        raise SystemExit(f"{path} has no `templates` list")
    return rows


def load_skills_registry(path: Path | None = None) -> list[dict]:
    """The `skills` list of skills.json, in file order — empty is allowed, a
    missing key is a malformed file."""
    path = SKILLS_REGISTRY_PATH if path is None else path
    data = json.loads(path.read_text())
    rows = data.get("skills")
    if not isinstance(rows, list):
        raise SystemExit(f"{path} has no `skills` list")
    return rows


def clone_entry(entry: dict, dest: Path, required_file: str) -> Path:
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
    if not (dest / required_file).exists():
        raise SystemExit(f"{repo} at {ref!r} has no {required_file} at its root")
    return dest


def clone_template(entry: dict, dest: Path) -> Path:
    return clone_entry(entry, dest, "recipe.json")


def fetch_templates(work_dir: Path, registry: list[dict] | None = None) -> list[tuple[dict, Path]]:
    """Clone every templates.json entry under `work_dir`, one directory per
    template. The checkout is named after the registry entry, so the
    directory a template is validated and indexed under is the name the
    registry lists it as."""
    registry = load_registry() if registry is None else registry
    out: list[tuple[dict, Path]] = []
    for entry in registry:
        out.append((entry, clone_template(entry, work_dir / entry["name"])))
    return out


def fetch_skills(work_dir: Path, registry: list[dict] | None = None) -> list[tuple[dict, Path]]:
    """Clone every skills.json entry under `work_dir`, one directory per skill,
    named after its entry — the name its frontmatter must equal."""
    registry = load_skills_registry() if registry is None else registry
    work_dir.mkdir(parents=True, exist_ok=True)
    return [(entry, clone_entry(entry, work_dir / entry["name"], "SKILL.md")) for entry in registry]


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
    entry, plus the commit that ref resolved to in the checkout this build
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


def split_yanked(yanked: set[str]) -> tuple[set[str], set[str]]:
    """(template specs, skill specs). A skill yank is `name@X.Y.Z`; everything
    else goes to the template tombstones, which refuse anything that is not
    `name@N` — so a typo in yanked.json still fails the build loudly."""
    skills = {spec for spec in yanked if SKILL_YANKED_SPEC_RE.match(spec)}
    return yanked - skills, skills


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


# --- skills ---------------------------------------------------------------------------------

_VALIDATOR = None


def validator():
    """scripts/validate.py, loaded by path. It is the one reader of SKILL.md: the
    build validates with it and reads the frontmatter back through it, so there is
    no second parser to drift. Resolved from this file, not REPO_ROOT (which tests
    point elsewhere)."""
    global _VALIDATOR
    if _VALIDATOR is None:
        spec = importlib.util.spec_from_file_location("butler_skills_validate", SCRIPTS_DIR / "validate.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _VALIDATOR = module
    return _VALIDATOR


def validate_skills(cloned: list[tuple[dict, Path]]) -> None:
    """Every listed skill must pass the validator in maintainer mode, or nothing is
    published. A failed build keeps the last Pages deploy live; skipping the skill
    instead would delist it from every butler."""
    v = validator()
    reserved = v.load_reserved()
    report: list[str] = []
    for entry, skill_dir in cloned:
        ok, result = v.validate_path(
            skill_dir, reserved, maintainer=True, json_mode=True, standalone=False, expected_kind="skill"
        )
        if not ok:
            report.append(f"skill {entry['name']} ({entry['repo']} @ {entry.get('ref') or 'main'}) does not validate:")
            report.extend(f"  ERROR {e}" for e in result["errors"])
    if report:
        raise SystemExit(
            "\n".join(report)
            + "\nrefusing to publish: fix the skill (or de-list it in skills.json) — the last deploy stays live until then"
        )


def collect_skill(skill_dir: Path, entry: dict) -> dict:
    """One `skills[]` row, read out of a checkout that already validated. `maxSteps`
    is on the row only when the skill sets one; `requires.skills` is always there,
    [] when the skill builds on no other."""
    v = validator()
    issues = v.Issues()
    fm = v.parse_skill_md((skill_dir / "SKILL.md").read_text(encoding="utf-8"), issues)
    if fm is None or issues.errors:
        raise SystemExit(f"{skill_dir}/SKILL.md: {'; '.join(issues.errors) or 'unreadable frontmatter'}")
    butler = fm["metadata"]["butler"]
    files = []
    for rel in v.skill_published_files(skill_dir):
        f = skill_dir / rel
        files.append({"path": rel, "sha256": sha256_file(f), "bytes": f.stat().st_size})
    row = {
        "name": fm["name"],
        "version": fm["version"],
        "description": fm["description"],
        "keywords": list(butler["keywords"]),
        "moneyMoving": butler["moneyMoving"],
    }
    if butler.get("maxSteps") is not None:
        row["maxSteps"] = butler["maxSteps"]
    row["requires"] = {
        "bins": list(butler["requires"]["bins"]),
        "skills": list(butler["requires"].get("skills", [])),
    }
    row["source"] = source_block(skill_dir, entry)
    row["files"] = files
    return row


def check_skill_dependencies(rows: list[dict], listed: set[str]) -> None:
    """Every skill a published row names in `requires.skills` must be published by
    this same build — a butler installs the required skills first, so a requirement
    the index does not serve (not listed, or listed with its current version
    yanked) could never install — and the requirements must not form a cycle. The
    rules are validate.py's (skill_dependency_errors, which `--all` applies to the
    listing), and a failure fails the whole build, like a skill that does not
    validate."""
    graph = {row["name"]: row["requires"]["skills"] for row in rows}
    problems = validator().skill_dependency_errors(graph, listed)
    if problems:
        raise SystemExit(
            "\n".join(f"skill {name}: ERROR metadata.butler.requires.skills: {msg}" for name, msg in problems)
            + "\nrefusing to publish: fix requires.skills (or list the skills it names) — the last deploy stays live until then"
        )


def skill_tombstone_entries(specs: set[str]) -> list[dict]:
    """A `{name, version, yanked: true, files: []}` row for every yanked skill
    version, listed or not: a butler that installed that version drops it on its
    next registry sync. Sorted by name, then numeric version."""
    rows: list[dict] = []
    for spec in specs:
        m = SKILL_YANKED_SPEC_RE.match(spec)
        if not m:
            raise SystemExit(f"yanked.json: {spec!r} is not <name>@<X.Y.Z>")
        rows.append({"name": m.group(1), "version": m.group(2), "yanked": True, "files": []})
    rows.sort(key=lambda r: (r["name"], tuple(int(p) for p in r["version"].split("."))))
    return rows


def live_index_url(flag: str | None) -> str:
    return flag or os.environ.get(LIVE_INDEX_ENV) or LIVE_INDEX_URL


def fetch_live_index(url: str) -> dict | None:
    """The index Pages serves right now, or None (with a warning) when it cannot be
    read — a first publish, an outage, or no network must not block a build."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            index = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        print(f"WARN  could not read the live index at {url} ({exc}); skipping the skill immutability check")
        return None
    if not isinstance(index, dict):
        print(f"WARN  the live index at {url} is not a JSON object; skipping the skill immutability check")
        return None
    return index


def _file_set(files) -> frozenset:
    if not isinstance(files, list):
        return frozenset()
    return frozenset(
        (f.get("path"), f.get("sha256"), f.get("bytes")) for f in files if isinstance(f, dict)
    )


def check_skill_immutability(rows: list[dict], live: dict | None) -> None:
    """A skill `name@version` the live index already serves must be republished with
    the same files, byte for byte; one it has yanked cannot come back at all. A
    changed skill is a new version."""
    live_rows = live.get("skills") if isinstance(live, dict) else None
    if not isinstance(live_rows, list):
        return
    by_ref: dict[str, dict] = {}
    for r in live_rows:
        if isinstance(r, dict) and isinstance(r.get("name"), str) and isinstance(r.get("version"), str):
            ref = f"{r['name']}@{r['version']}"
            # A tombstone for the same ref outranks a live row: once yanked, always yanked.
            if ref not in by_ref or r.get("yanked"):
                by_ref[ref] = r
    for row in rows:
        ref = f"{row['name']}@{row['version']}"
        old = by_ref.get(ref)
        if old is None:
            continue
        if old.get("yanked"):
            raise SystemExit(
                f"refusing to republish {ref}: the live index has it yanked — publish a new version instead"
            )
        before, after = _file_set(old.get("files")), _file_set(row["files"])
        if before != after:
            changed = sorted({p for p, _, _ in before ^ after if p})
            raise SystemExit(
                f"refusing to republish {ref} with different bytes than the live index serves "
                f"({', '.join(changed)}) — bump `version` in SKILL.md and add its CHANGELOG.md entry instead"
            )


def write_skill_dist_files(skill_dir: Path, row: dict, dist_root: Path, dry_run: bool) -> None:
    version_dir = dist_root / "skills" / row["name"] / row["version"]
    paths = [f["path"] for f in row["files"]]
    if version_dir.exists():
        for rel in paths:
            dst = version_dir / rel
            if not dst.exists() or not filecmp.cmp(skill_dir / rel, dst, shallow=False):
                raise SystemExit(
                    f"refusing to overwrite existing published version {row['name']}@{row['version']}: "
                    f"{dst} differs (bump the version instead)"
                )
        return
    if dry_run:
        return
    for rel in paths:
        dst = version_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_dir / rel, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dist/index.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--live-index",
        help=f"the published index to hold skill versions immutable against (default: ${LIVE_INDEX_ENV} or {LIVE_INDEX_URL})",
    )
    args = parser.parse_args()

    yanked = load_yanked()
    template_yanked, skill_yanked = split_yanked(yanked)
    registry = load_registry()
    skill_registry = load_skills_registry()

    seen_refs: dict[str, dict] = {}

    # The clones live only for this build: the registry stores links, not files.
    with tempfile.TemporaryDirectory(prefix="butler-skills-build-") as tmp:
        cloned = fetch_templates(Path(tmp), registry)

        entries = []
        for entry, d in cloned:
            collected = collect_template(d, entry)
            ref = f"{collected['name']}@{collected['version']}"
            if ref in seen_refs:
                raise SystemExit(f"two templates resolve to the same {ref} — {entry['repo']} and {seen_refs[ref]['repo']}")
            seen_refs[ref] = entry
            entries.append(collected)

        tombstones = tombstone_entries(template_yanked, entries)
        aliases = build_aliases(cloned)

        # Skills clone under a directory no template name can take (names never
        # start with a dot), validate as a set, and only then are read.
        cloned_skills = fetch_skills(Path(tmp) / ".skills", skill_registry)
        validate_skills(cloned_skills)
        skill_rows: list[tuple[dict, Path]] = []
        seen_skills: dict[str, dict] = {}
        for entry, d in cloned_skills:
            row = collect_skill(d, entry)
            ref = f"{row['name']}@{row['version']}"
            if ref in seen_skills:
                raise SystemExit(f"two skills resolve to the same {ref} — {entry['repo']} and {seen_skills[ref]['repo']}")
            seen_skills[ref] = entry
            if ref in skill_yanked:
                print(f"WARN  {ref} is listed in skills.json but yanked in yanked.json — publishing only its tombstone")
                continue
            skill_rows.append((row, d))
        check_skill_dependencies([row for row, _ in skill_rows], {e["name"] for e in skill_registry})
        skill_tombstones = skill_tombstone_entries(skill_yanked)
        if skill_rows:
            check_skill_immutability([row for row, _ in skill_rows], fetch_live_index(live_index_url(args.live_index)))

        index = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "templates": entries + tombstones,
            "aliases": aliases,
            "skills": [row for row, _ in skill_rows] + skill_tombstones,
        }

        dist_root = REPO_ROOT / "dist"
        for entry, d in cloned:
            recipe = json.loads((d / "recipe.json").read_text())
            write_dist_files(d, recipe["id"], recipe["version"], dist_root, args.dry_run)
        for row, d in skill_rows:
            write_skill_dist_files(d, row, dist_root, args.dry_run)

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
    skill_summary = f"{len(skill_rows)} skill(s) indexed"
    if skill_tombstones:
        skill_summary += f", plus {len(skill_tombstones)} yanked tombstone(s)"
    print(skill_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
