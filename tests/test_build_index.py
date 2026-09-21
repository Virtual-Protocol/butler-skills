"""test_build_index.py — pytest coverage for scripts/build_index.py.

Templates are not checked out in this repo: each build reads templates.json,
clones every entry at its ref into a throwaway directory and indexes that. So
the fixtures here are synthetic — a git repo built in tmp_path standing in
for one of those clones — which keeps the suite offline and fast while still
exercising the real thing: recipe.json parsing, the per-file sha256 that lets
a container verify what it fetched, the `source` block (repo and ref from the
registry entry, commit resolved from the checkout), the yanked tombstones,
and the `supersedes` -> `aliases` flattening.
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

RECIPE_JSON = json.dumps({
    "id": "dca",
    "version": 2,
    "description": "Buy a fixed dollar amount of one token on a schedule.",
    "keywords": ["dca", "schedule"],
    "triggers": ["timer"],
    "supersedes": ["dca@1"],
    "keys": ["buy:<duty>:slot:<slot>"],
    "params": {"type": "object", "properties": {"TOKEN": {"type": "string"}}},
})

ENTRY = {"name": "dca", "repo": "https://github.com/Virtual-Protocol/butler-skill-dca", "ref": "main"}

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env=GIT_ENV).stdout


def make_checkout(tmp_path: Path, recipe_json: str = RECIPE_JSON, extra: dict[str, str] | None = None) -> Path:
    """A template checkout of the shape build_index gets back from
    clone_skill(): a real git repo, so the commit in the source block has
    something to resolve against."""
    d = tmp_path / "checkout"
    d.mkdir()
    (d / "recipe.json").write_text(recipe_json)
    (d / "duty.py").write_text("import bevo\nbevo.log('x')\n")
    (d / "README.md").write_text("# dca\n")
    for name, text in (extra or {}).items():
        (d / name).write_text(text)
    git("init", "-q", "-b", "main", cwd=d)
    git("add", "-A", cwd=d)
    git("commit", "-q", "-m", "init", cwd=d)
    return d


def head(d: Path) -> str:
    return git("rev-parse", "HEAD", cwd=d).strip()


def test_collect_template_reads_recipe_json(tmp_path):
    d = make_checkout(tmp_path)
    entry = build_index.collect_template(d, ENTRY)
    assert entry["name"] == "dca"
    assert entry["version"] == 2
    assert entry["description"] == "Buy a fixed dollar amount of one token on a schedule."
    assert entry["keywords"] == ["dca", "schedule"]
    assert entry["triggers"] == ["timer"]
    assert entry["keys"] == ["buy:<duty>:slot:<slot>"]


def test_collect_template_hashes_every_published_file(tmp_path):
    d = make_checkout(tmp_path, extra={"NOTES.md": "not published\n"})
    entry = build_index.collect_template(d, ENTRY)
    assert {f["path"] for f in entry["files"]} == {"recipe.json", "duty.py", "README.md"}
    for f in entry["files"]:
        blob = (d / f["path"]).read_bytes()
        assert f["sha256"] == hashlib.sha256(blob).hexdigest()
        assert f["sha256"] == build_index.sha256_file(d / f["path"])
        assert f["bytes"] == len(blob) > 0


def test_source_block_pins_the_resolved_commit_and_short_repo(tmp_path):
    d = make_checkout(tmp_path)
    src = build_index.source_block(d, ENTRY)
    assert set(src) == {"repo", "ref", "commit"}
    assert src["repo"] == "Virtual-Protocol/butler-skill-dca"
    assert src["ref"] == ENTRY["ref"]
    assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]), src["commit"]
    assert src["commit"] == head(d)


def test_collect_template_carries_the_source_block(tmp_path):
    d = make_checkout(tmp_path)
    entry = build_index.collect_template(d, ENTRY)
    assert entry["source"] == build_index.source_block(d, ENTRY)


def test_a_new_commit_on_the_ref_changes_the_pinned_commit(tmp_path):
    d = make_checkout(tmp_path)
    before = build_index.source_block(d, ENTRY)
    (d / "recipe.json").write_text(RECIPE_JSON.replace('"version": 2', '"version": 3'))
    git("commit", "-qam", "bump", cwd=d)
    after = build_index.source_block(d, ENTRY)
    assert after["ref"] == before["ref"] == "main"
    assert after["commit"] != before["commit"]
    assert build_index.collect_template(d, ENTRY)["version"] == 3


def test_build_aliases_flattens_supersedes(tmp_path):
    d = make_checkout(tmp_path)
    aliases = build_index.build_aliases([(ENTRY, d)])
    assert aliases == [{"ref": "dca@1", "supersededBy": "dca@2"}]


def test_build_aliases_is_empty_with_no_supersedes(tmp_path):
    no_supersedes = json.dumps({**json.loads(RECIPE_JSON), "supersedes": []})
    d = make_checkout(tmp_path, recipe_json=no_supersedes)
    assert build_index.build_aliases([(ENTRY, d)]) == []


def test_yanked_version_without_a_registry_entry_is_published_as_a_tombstone(tmp_path):
    live = [build_index.collect_template(make_checkout(tmp_path), ENTRY)]
    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    entry_schema = schema["properties"]["templates"]["items"]
    tombstones = build_index.tombstone_entries({"gone-template@2", "gone-template@1"}, live)
    assert [(t["name"], t["version"]) for t in tombstones] == [("gone-template", 1), ("gone-template", 2)]
    for t in tombstones:
        assert t["files"] == [] and "source" not in t
        assert set(entry_schema["required"]) <= set(t) <= set(entry_schema["properties"])
        assert len(t["description"]) <= entry_schema["properties"]["description"]["maxLength"]
    # a yanked version of a template that is still listed (at any version) is
    # never tombstoned: its live entry is what supersedes it
    assert build_index.tombstone_entries({f"{live[0]['name']}@1"}, live) == []
    assert build_index.tombstone_entries({f"{live[0]['name']}@{live[0]['version']}"}, live) == []
    assert build_index.tombstone_entries(set(), live) == []


def test_malformed_yanked_spec_fails_loudly():
    for bad in ("gone-template", "gone-template@x", "Gone@1", "gone-template@1.0.0"):
        try:
            build_index.tombstone_entries({bad}, [])
        except SystemExit as e:
            assert "yanked.json" in str(e) and bad in str(e)
        else:
            raise AssertionError(f"expected SystemExit for {bad!r}")


def test_real_yanked_json_entries_without_a_registry_entry_are_tombstoned():
    """The checked-in yanked.json against the checked-in templates.json — no
    clone needed: whether a yank becomes a tombstone depends only on whether
    the registry still lists that name."""
    yanked = build_index.load_yanked()
    live = [{"name": row["name"]} for row in build_index.load_registry()]
    live_names = {e["name"] for e in live}
    expected = {spec for spec in yanked if spec.split("@", 1)[0] not in live_names}
    tombstones = build_index.tombstone_entries(yanked, live)
    assert {f"{t['name']}@{t['version']}" for t in tombstones} == expected


def test_two_templates_resolving_to_the_same_ref_are_refused(tmp_path, monkeypatch):
    """The duplicate-ref guard lives inside main(); exercise it end to end
    against a two-entry registry whose recipe.json both say dca@2 (a name
    typo in templates.json, or two forks of the same template)."""
    reg = tmp_path / "templates.json"
    reg.write_text(json.dumps({"templates": [
        {"name": "dca", "repo": _local_repo(tmp_path / "a", RECIPE_JSON), "ref": "main"},
        {"name": "dca-fork", "repo": _local_repo(tmp_path / "b", RECIPE_JSON), "ref": "main"},
    ]}))
    monkeypatch.setattr(build_index, "REGISTRY_PATH", reg)
    monkeypatch.setattr(build_index, "REPO_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["build_index.py", "--dry-run"])
    try:
        build_index.main()
        raised = False
    except SystemExit as e:
        raised = "resolve to the same" in str(e)
    assert raised, "two registry entries whose recipe.json both say dca@2 must refuse the build"


def _local_repo(root: Path, recipe_json: str) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / "recipe.json").write_text(recipe_json)
    (root / "duty.py").write_text("import bevo\nbevo.log('x')\n")
    (root / "README.md").write_text("# fixture\n")
    git("init", "-q", "-b", "main", cwd=root)
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    return root.as_uri()


def test_index_schema_pins_schema_version_3():
    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    assert schema["properties"]["schemaVersion"]["enum"] == [3]
    src = schema["properties"]["templates"]["items"]["properties"]["source"]
    assert set(src["required"]) == {"repo", "ref", "commit"}


def test_build_index_dry_run_end_to_end(tmp_path, monkeypatch):
    """A full --dry-run against a synthetic one-entry registry, proving
    main() wires load_registry -> fetch_skills -> collect_template ->
    tombstone_entries -> build_aliases -> the index dict together."""
    reg = tmp_path / "templates.json"
    reg.write_text(json.dumps({"templates": [ENTRY | {"repo": _local_repo(tmp_path / "src", RECIPE_JSON)}]}))
    monkeypatch.setattr(build_index, "REGISTRY_PATH", reg)
    monkeypatch.setattr(build_index, "YANKED_PATH", tmp_path / "no-such-yanked.json")
    monkeypatch.setattr(build_index, "REPO_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["build_index.py", "--dry-run"])
    assert build_index.main() == 0
    assert not (tmp_path / "dist" / "templates").exists()  # dry-run writes nothing
