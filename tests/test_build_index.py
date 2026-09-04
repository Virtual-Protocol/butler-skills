"""test_build_index.py — pytest coverage for scripts/build_index.py, including
the git-backed `source` block (repo URL, 40-hex pinned commit, `v<version>`
ref) that every entry carries while `schemaVersion` stays 1."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_index_module():
    spec = importlib.util.spec_from_file_location("butler_skills_build_index", REPO_ROOT / "scripts" / "build_index.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_index = _load_build_index_module()


def test_collect_skill_butler_copytrade():
    entry = build_index.collect_skill(REPO_ROOT / "skills" / "butler-copytrade", yanked=set())
    assert entry["name"] == "butler-copytrade"
    assert entry["version"] == "1.0.1"
    assert entry["tier"] == "on-demand"
    assert "one-off" in entry["modes"] and "duty" in entry["modes"]
    assert entry["moneyMoving"] is True
    assert entry["yanked"] is False
    paths = {f["path"] for f in entry["files"]}
    assert {"SKILL.md", "duty.py", "CHANGELOG.md"} <= paths
    for f in entry["files"]:
        assert len(f["sha256"]) == 64
        assert f["bytes"] > 0


def test_collect_skill_respects_yanked():
    entry = build_index.collect_skill(
        REPO_ROOT / "skills" / "butler-copytrade", yanked={"butler-copytrade@1.0.1"}
    )
    assert entry["yanked"] is True


def test_regenerate_catalog_contains_all_skills_and_links_their_repos():
    entries = [build_index.collect_skill(d, yanked=set()) for d in build_index.list_skill_dirs()]
    catalog = build_index.regenerate_catalog(entries)
    assert "| Repo |" in catalog
    for e in entries:
        assert e["name"] in catalog
        repo = e["source"]["repo"]
        assert f"[{build_index.repo_slug(repo)}]({repo}/tree/{e['source']['ref']})" in catalog


def test_list_skill_dirs_matches_gitmodules():
    gitmodules = build_index.parse_gitmodules()
    declared = sorted(p for p in gitmodules if p.startswith("skills/"))
    assert declared, ".gitmodules declares no skills/<name> submodules"
    assert [d.relative_to(REPO_ROOT).as_posix() for d in build_index.list_skill_dirs()] == declared


def test_source_block_has_repo_40hex_commit_and_tag_ref():
    gitmodules = build_index.parse_gitmodules()
    for d in build_index.list_skill_dirs():
        entry = build_index.collect_skill(d, yanked=set(), gitmodules=gitmodules)
        src = entry["source"]
        assert set(src) == {"repo", "commit", "ref"}
        assert src["repo"] == gitmodules[d.relative_to(REPO_ROOT).as_posix()]["url"]
        assert re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", src["repo"]), src["repo"]
        assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]), src["commit"]
        assert src["ref"] == f"v{entry['version']}"
        # the commit is what the submodule checkout actually sits on
        head = subprocess.run(
            ["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert src["commit"] == head
        # ... and what the superproject's gitlink records (the reviewed pin)
        gitlink = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-tree", "HEAD", d.relative_to(REPO_ROOT).as_posix()],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        assert gitlink[:2] == ["160000", "commit"], gitlink
        assert gitlink[2] == src["commit"]


def test_source_block_is_additive_files_and_sha256_unchanged():
    entry = build_index.collect_skill(REPO_ROOT / "skills" / "butler-copytrade", yanked=set())
    assert {f["path"] for f in entry["files"]} == {"SKILL.md", "duty.py", "CHANGELOG.md"}
    for f in entry["files"]:
        assert f["sha256"] == build_index.sha256_file(REPO_ROOT / "skills" / "butler-copytrade" / f["path"])


def _live_entries() -> list[dict]:
    gitmodules = build_index.parse_gitmodules()
    return [build_index.collect_skill(d, yanked=set(), gitmodules=gitmodules) for d in build_index.list_skill_dirs()]


def test_yanked_version_without_a_submodule_is_published_as_a_tombstone():
    """Removing a skill's submodule must not silently drop its yank: the container's hub
    client only disables a skill on an index entry carrying yanked:true, and never
    installs one, so the tombstone needs no files[] and no source."""
    live = _live_entries()
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
    # a yanked version of a skill that is still pinned (at any version) is never
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


def test_real_yanked_json_entries_without_a_submodule_are_tombstoned():
    yanked = build_index.load_yanked()
    live = _live_entries()
    live_names = {e["name"] for e in live}
    expected = {spec for spec in yanked if spec.split("@", 1)[0] not in live_names}
    tombstones = build_index.tombstone_entries(yanked, live)
    assert {f"{t['name']}@{t['version']}" for t in tombstones} == expected
    catalog = build_index.regenerate_catalog(live + tombstones)
    for t in tombstones:
        assert f"| `{t['name']}` (yanked) | {t['version']} | — |" in catalog


def test_uninitialised_submodule_fails_loudly(tmp_path):
    root = tmp_path / "registry"
    (root / "skills" / "foo").mkdir(parents=True)
    (root / ".gitmodules").write_text('[submodule "skills/foo"]\n\tpath = skills/foo\n\turl = https://github.com/x/foo\n')
    try:
        build_index.list_skill_dirs(root)
    except SystemExit as e:
        assert "git submodule update --init --recursive" in str(e)
    else:
        raise AssertionError("expected SystemExit for an uninitialised submodule")


def test_dry_run_full_repo_schema_version_stays_1(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "build_index.py"), "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skill(s) indexed" in proc.stdout
    assert '"schemaVersion": 1,' in proc.stdout


def test_index_schema_allows_source_and_pins_schema_version_1():
    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    assert schema["properties"]["schemaVersion"]["enum"] == [1]
    src = schema["properties"]["skills"]["items"]["properties"]["source"]
    assert set(src["required"]) == {"repo", "commit", "ref"}
    assert "source" not in schema["properties"]["skills"]["items"]["required"]  # additive, optional
