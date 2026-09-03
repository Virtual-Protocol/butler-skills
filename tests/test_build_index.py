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


def test_collect_skill_bevo_copytrade():
    entry = build_index.collect_skill(REPO_ROOT / "skills" / "bevo-copytrade", yanked=set())
    assert entry["name"] == "bevo-copytrade"
    assert entry["version"] == "1.0.0"
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
        REPO_ROOT / "skills" / "bevo-copytrade", yanked={"bevo-copytrade@1.0.0"}
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
    entry = build_index.collect_skill(REPO_ROOT / "skills" / "bevo-copytrade", yanked=set())
    assert {f["path"] for f in entry["files"]} == {"SKILL.md", "duty.py", "CHANGELOG.md"}
    for f in entry["files"]:
        assert f["sha256"] == build_index.sha256_file(REPO_ROOT / "skills" / "bevo-copytrade" / f["path"])


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
        [sys.executable, str(REPO_ROOT / "scripts" / "build_index.py"), "--dry-run", "--channel", "canary"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skill(s) indexed for channel=canary" in proc.stdout
    assert '"schemaVersion": 1,' in proc.stdout


def test_index_schema_allows_source_and_pins_schema_version_1():
    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    assert schema["properties"]["schemaVersion"]["enum"] == [1]
    src = schema["properties"]["skills"]["items"]["properties"]["source"]
    assert set(src["required"]) == {"repo", "commit", "ref"}
    assert "source" not in schema["properties"]["skills"]["items"]["required"]  # additive, optional
