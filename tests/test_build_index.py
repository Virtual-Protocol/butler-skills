"""test_build_index.py — pytest coverage for scripts/build_index.py.

Skills are no longer checked out in this repo: each build reads skills.json,
clones every entry at its ref into a throwaway directory and indexes that. So
the fixtures here are synthetic — a git repo built in tmp_path standing in for
one of those clones — which keeps the suite offline and fast while still
exercising the real thing: frontmatter parsing, the per-file sha256 that lets
a container verify what it fetched, the `source` block (repo and ref from the
registry entry, commit resolved from the checkout), and the yanked tombstones.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_index_module():
    spec = importlib.util.spec_from_file_location("butler_skills_build_index", REPO_ROOT / "scripts" / "build_index.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_index = _load_build_index_module()

SKILL_MD = (
    "---\n"
    "name: butler-alpha\n"
    "description: Example skill used by the build_index tests.\n"
    "version: 1.2.3\n"
    'metadata: {"butler":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,'
    '"keywords":["alpha"],"params":[{"name":"amount"}],"requires":{"routes":["/butler-read/me"]}}}\n'
    "---\n\n"
    "## When to use\nExample.\n"
)

ENTRY = {"name": "butler-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"}

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env=GIT_ENV).stdout


def make_checkout(tmp_path: Path, skill_md: str = SKILL_MD, extra: dict[str, str] | None = None) -> Path:
    """A skill checkout of the shape build_index gets back from clone_skill():
    a real git repo, so the commit in the source block has something to resolve
    against."""
    d = tmp_path / "checkout"
    d.mkdir()
    (d / "SKILL.md").write_text(skill_md)
    (d / "duty.py").write_text("# duty\n")
    (d / "CHANGELOG.md").write_text("# Changelog\n")
    for name, text in (extra or {}).items():
        (d / name).write_text(text)
    git("init", "-q", "-b", "main", cwd=d)
    git("add", "-A", cwd=d)
    git("commit", "-q", "-m", "init", cwd=d)
    return d


def head(d: Path) -> str:
    return git("rev-parse", "HEAD", cwd=d).strip()


def test_collect_skill_reads_frontmatter(tmp_path):
    d = make_checkout(tmp_path)
    entry = build_index.collect_skill(d, set(), ENTRY)
    assert entry["name"] == "butler-alpha"
    assert entry["version"] == "1.2.3"
    assert entry["description"] == "Example skill used by the build_index tests."
    assert entry["tier"] == "on-demand"
    assert entry["modes"] == ["one-off", "duty"]
    assert entry["moneyMoving"] is True
    assert entry["keywords"] == ["alpha"]
    assert entry["requires"] == {"routes": ["/butler-read/me"]}
    assert entry["yanked"] is False


def test_collect_skill_hashes_every_published_file(tmp_path):
    """files[] plus sha256 is what a container verifies after fetching, so it
    covers exactly the published files and nothing else in the checkout."""
    d = make_checkout(tmp_path, extra={"README.md": "not published\n"})
    entry = build_index.collect_skill(d, set(), ENTRY)
    assert {f["path"] for f in entry["files"]} == {"SKILL.md", "duty.py", "CHANGELOG.md"}
    for f in entry["files"]:
        blob = (d / f["path"]).read_bytes()
        assert f["sha256"] == hashlib.sha256(blob).hexdigest()
        assert f["sha256"] == build_index.sha256_file(d / f["path"])
        assert f["bytes"] == len(blob) > 0


def test_collect_skill_respects_yanked(tmp_path):
    d = make_checkout(tmp_path)
    assert build_index.collect_skill(d, {"butler-alpha@1.2.3"}, ENTRY)["yanked"] is True
    assert build_index.collect_skill(d, {"butler-alpha@1.0.0"}, ENTRY)["yanked"] is False


def test_source_block_pins_the_resolved_commit(tmp_path):
    """repo and ref come from the registry entry; the commit is resolved from
    the checkout that was cloned for this build. The ref may be a branch, so
    the commit — not the ref — is what the index pins."""
    d = make_checkout(tmp_path)
    src = build_index.source_block(d, ENTRY)
    assert set(src) == {"repo", "commit", "ref"}
    assert src["repo"] == ENTRY["repo"]
    assert src["ref"] == ENTRY["ref"]
    assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]), src["commit"]
    assert src["commit"] == head(d)


def test_collect_skill_carries_the_source_block(tmp_path):
    d = make_checkout(tmp_path)
    entry = build_index.collect_skill(d, set(), ENTRY)
    assert entry["source"] == build_index.source_block(d, ENTRY)


def test_a_new_commit_on_the_ref_changes_the_pinned_commit(tmp_path):
    """The point of the link registry: the ref stays put, the commit moves, and
    the next build republishes whatever the skill repo merged."""
    d = make_checkout(tmp_path)
    before = build_index.source_block(d, ENTRY)
    (d / "SKILL.md").write_text(SKILL_MD.replace("version: 1.2.3", "version: 1.2.4"))
    git("commit", "-qam", "bump", cwd=d)
    after = build_index.source_block(d, ENTRY)
    assert after["ref"] == before["ref"] == "main"
    assert after["commit"] != before["commit"]
    assert build_index.collect_skill(d, set(), ENTRY)["version"] == "1.2.4"


def test_regenerate_catalog_contains_all_skills_and_links_their_repos(tmp_path):
    d = make_checkout(tmp_path)
    entries = [build_index.collect_skill(d, set(), ENTRY)]
    catalog = build_index.regenerate_catalog(entries)
    assert "| Repo |" in catalog
    for e in entries:
        assert e["name"] in catalog
        src = e["source"]
        assert f"[{build_index.repo_slug(src['repo'])}]({src['repo']}/tree/{src['ref']})" in catalog


def test_yanked_version_without_a_registry_entry_is_published_as_a_tombstone(tmp_path):
    """Removing a skill from skills.json must not silently drop its yank: the
    container's hub client only disables a skill on an index entry carrying
    yanked:true, and never installs one, so the tombstone needs no files[] and
    no source."""
    live = [build_index.collect_skill(make_checkout(tmp_path), set(), ENTRY)]
    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    entry_schema = schema["properties"]["skills"]["items"]
    tombstones = build_index.tombstone_entries({"gone-skill@1.1.0", "gone-skill@1.0.0"}, live)
    assert [(t["name"], t["version"]) for t in tombstones] == [("gone-skill", "1.0.0"), ("gone-skill", "1.1.0")]
    for t in tombstones:
        assert t["yanked"] is True
        assert t["files"] == [] and "source" not in t
        assert set(entry_schema["required"]) <= set(t) <= set(entry_schema["properties"])
        assert len(t["description"]) <= entry_schema["properties"]["description"]["maxLength"]
        assert t["tier"] in entry_schema["properties"]["tier"]["enum"]
    # a yanked version of a skill that is still listed (at any version) is never
    # tombstoned: its live entry is what un-yanks and updates the container
    assert build_index.tombstone_entries({f"{live[0]['name']}@0.0.1"}, live) == []
    assert build_index.tombstone_entries({f"{live[0]['name']}@{live[0]['version']}"}, live) == []
    assert build_index.tombstone_entries(set(), live) == []


def test_malformed_yanked_spec_fails_loudly():
    for bad in ("gone-skill", "gone-skill@1.0", "Gone@1.0.0", "gone-skill@v1.0.0"):
        try:
            build_index.tombstone_entries({bad}, [])
        except SystemExit as e:
            assert "yanked.json" in str(e) and bad in str(e)
        else:
            raise AssertionError(f"expected SystemExit for {bad!r}")


def test_real_yanked_json_entries_without_a_registry_entry_are_tombstoned():
    """The checked-in yanked.json against the checked-in skills.json — no
    clone needed: whether a yank becomes a tombstone depends only on whether
    the registry still lists that name."""
    yanked = build_index.load_yanked()
    live = [{"name": row["name"], "version": "9.9.9", "yanked": False} for row in build_index.load_registry()]
    live_names = {e["name"] for e in live}
    expected = {spec for spec in yanked if spec.split("@", 1)[0] not in live_names}
    tombstones = build_index.tombstone_entries(yanked, live)
    assert {f"{t['name']}@{t['version']}" for t in tombstones} == expected
    catalog = build_index.regenerate_catalog(live + tombstones)
    for t in tombstones:
        assert f"| `{t['name']}` (yanked) | {t['version']} | — |" in catalog


def test_index_schema_allows_source_and_pins_schema_version_1():
    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    assert schema["properties"]["schemaVersion"]["enum"] == [1]
    src = schema["properties"]["skills"]["items"]["properties"]["source"]
    assert set(src["required"]) == {"repo", "commit", "ref"}
    assert "source" not in schema["properties"]["skills"]["items"]["required"]  # additive, optional
