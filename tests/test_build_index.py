"""test_build_index.py — pytest coverage for scripts/build_index.py.

Templates and skills are not checked out in this repo: each build reads
templates.json and skills.json, clones every entry at its ref into a throwaway
directory and indexes that. So the fixtures here are synthetic — a git repo
built in tmp_path standing in for one of those clones — which keeps the suite
offline and fast while still exercising the real thing: recipe.json parsing,
the per-file sha256 that lets a container verify what it fetched, the `source`
block (repo and ref from the registry entry, commit resolved from the
checkout), the yanked tombstones, the `supersedes` -> `aliases` flattening,
and for skills: validation that aborts the build, cross-build immutability
against the live index, and the `skills[]` rows and tombstones.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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
    clone_template(): a real git repo, so the commit in the source block has
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
    (tmp_path / "skills.json").write_text(json.dumps({"skills": []}))
    monkeypatch.setattr(build_index, "SKILLS_REGISTRY_PATH", tmp_path / "skills.json")
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
    main() wires load_registry -> fetch_templates -> collect_template ->
    tombstone_entries -> build_aliases -> the index dict together."""
    reg = tmp_path / "templates.json"
    reg.write_text(json.dumps({"templates": [ENTRY | {"repo": _local_repo(tmp_path / "src", RECIPE_JSON)}]}))
    monkeypatch.setattr(build_index, "REGISTRY_PATH", reg)
    (tmp_path / "skills.json").write_text(json.dumps({"skills": []}))  # offline: never the real listing
    monkeypatch.setattr(build_index, "SKILLS_REGISTRY_PATH", tmp_path / "skills.json")
    monkeypatch.setattr(build_index, "YANKED_PATH", tmp_path / "no-such-yanked.json")
    monkeypatch.setattr(build_index, "REPO_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["build_index.py", "--dry-run"])
    assert build_index.main() == 0
    assert not (tmp_path / "dist" / "templates").exists()  # dry-run writes nothing


# === skills ================================================================================


SKILL_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "skills"


def _skill_repo(root: Path, fixture: str = "valid") -> str:
    """A real local git repo holding one skill fixture, named after it on disk."""
    shutil.copytree(SKILL_FIXTURES / fixture, root)
    git("init", "-q", "-b", "main", cwd=root)
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    return root.as_uri()


def _build(tmp_path: Path, monkeypatch, *, skills: list[dict], templates: list[dict] | None = None,
           yanked: list[str] | None = None, live: dict | None = None, dry_run: bool = False) -> dict:
    """Run main() in a tmp REPO_ROOT against synthetic listings; return the index it
    wrote (or would have written). `live` is served as the live index via file://."""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    if templates is None:
        templates = [ENTRY | {"repo": _local_repo(tmp_path / "tmpl-src", RECIPE_JSON)}]
    (root / "templates.json").write_text(json.dumps({"templates": templates}))
    (root / "skills.json").write_text(json.dumps({"skills": skills}))
    (root / "yanked.json").write_text(json.dumps({"yanked": yanked or []}))
    live_path = tmp_path / "live-index.json"
    if live is not None:
        live_path.write_text(json.dumps(live))
    monkeypatch.setattr(build_index, "REPO_ROOT", root)
    monkeypatch.setattr(build_index, "REGISTRY_PATH", root / "templates.json")
    monkeypatch.setattr(build_index, "SKILLS_REGISTRY_PATH", root / "skills.json")
    monkeypatch.setattr(build_index, "YANKED_PATH", root / "yanked.json")
    monkeypatch.setenv(build_index.LIVE_INDEX_ENV, live_path.as_uri())
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["build_index.py", *(["--dry-run"] if dry_run else [])])
    assert build_index.main() == 0
    index_path = root / "dist" / "index.json"
    return json.loads(index_path.read_text()) if index_path.exists() else {}


def test_the_templates_part_is_the_same_with_or_without_skills(tmp_path, monkeypatch):
    """Adding the skill kind must not move a byte of what a template-only butler reads."""
    src = _local_repo(tmp_path / "tmpl-src", RECIPE_JSON)
    templates = [ENTRY | {"repo": src}]
    without = _build(tmp_path / "a", monkeypatch, skills=[], templates=templates)
    with_skill = _build(tmp_path / "b", monkeypatch, templates=templates,
                        skills=[{"name": "valid", "repo": _skill_repo(tmp_path / "skill-src"), "ref": "main"}])
    assert list(without) == ["schemaVersion", "generatedAt", "templates", "aliases", "skills"]
    assert without["schemaVersion"] == with_skill["schemaVersion"] == 3
    assert without["templates"] == with_skill["templates"] == [build_index.collect_template(tmp_path / "tmpl-src", templates[0])]
    assert without["aliases"] == with_skill["aliases"] == [{"ref": "dca@1", "supersededBy": "dca@2"}]
    assert without["skills"] == [] and len(with_skill["skills"]) == 1


def test_a_skill_row_and_its_published_files(tmp_path, monkeypatch):
    src = tmp_path / "skill-src"
    index = _build(tmp_path, monkeypatch, skills=[{"name": "valid", "repo": _skill_repo(src), "ref": "main"}])
    [row] = index["skills"]
    assert row["name"] == "valid" and row["version"] == "1.0.0"
    assert row["description"].startswith("A minimal, fully compliant skill fixture")
    assert row["keywords"] == ["fixture", "buy once"] and row["moneyMoving"] is True
    assert row["requires"] == {"bins": ["acp", "bevo-read", "bevo-notify"], "skills": []}
    assert "maxSteps" not in row  # only a skill that sets one carries it
    assert row["source"] == {"repo": src.as_uri().split("://", 1)[1].strip("/"), "ref": "main", "commit": head(src)}
    assert [f["path"] for f in row["files"]] == ["SKILL.md", "references/sizing.md"]  # README/CHANGELOG stay home
    dist = tmp_path / "repo" / "dist" / "skills" / "valid" / "1.0.0"
    for f in row["files"]:
        blob = (dist / f["path"]).read_bytes()
        assert blob == (src / f["path"]).read_bytes()
        assert f["sha256"] == hashlib.sha256(blob).hexdigest() and f["bytes"] == len(blob)
    assert not (dist / "README.md").exists() and not (dist / "CHANGELOG.md").exists()

    schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())
    skill_schema = schema["definitions"]["skill"]
    assert set(skill_schema["required"]) <= set(row) <= set(skill_schema["properties"])
    assert re.fullmatch(skill_schema["properties"]["files"]["items"]["properties"]["path"]["pattern"], "references/sizing.md")


def test_an_invalid_skill_aborts_the_whole_build(tmp_path, monkeypatch):
    """Failing loudly keeps the last deploy live; skipping would delist the skill fleet-wide."""
    bad = _skill_repo(tmp_path / "yaml-colon", "yaml-colon")
    with pytest.raises(SystemExit) as e:
        _build(tmp_path, monkeypatch, skills=[{"name": "yaml-colon", "repo": bad, "ref": "main"}])
    assert "skill yaml-colon" in str(e.value) and "ERROR description:" in str(e.value)
    assert "the last deploy stays live" in str(e.value)
    assert not (tmp_path / "repo" / "dist").exists()


def test_a_skill_version_is_immutable_across_builds(tmp_path, monkeypatch):
    src = tmp_path / "skill-src"
    listing = [{"name": "valid", "repo": _skill_repo(src), "ref": "main"}]
    first = _build(tmp_path / "one", monkeypatch, skills=listing)

    # the same bytes under the same version republish fine
    again = _build(tmp_path / "two", monkeypatch, skills=listing, live=first)
    assert again["skills"] == first["skills"]

    # changed bytes under a version the live index serves are refused
    (src / "SKILL.md").write_text((src / "SKILL.md").read_text().replace("right now", "right away"))
    git("commit", "-qam", "reword", cwd=src)
    with pytest.raises(SystemExit) as e:
        _build(tmp_path / "three", monkeypatch, skills=listing, live=first)
    assert "refusing to republish valid@1.0.0 with different bytes" in str(e.value) and "SKILL.md" in str(e.value)

    # a version the live index has yanked cannot come back
    yanked_live = {**first, "skills": [{"name": "valid", "version": "1.0.0", "yanked": True, "files": []}]}
    with pytest.raises(SystemExit) as e:
        _build(tmp_path / "four", monkeypatch, skills=listing, live=yanked_live)
    assert "the live index has it yanked" in str(e.value)


def test_an_unreachable_live_index_is_a_warning(tmp_path, monkeypatch, capsys):
    listing = [{"name": "valid", "repo": _skill_repo(tmp_path / "skill-src"), "ref": "main"}]
    index = _build(tmp_path, monkeypatch, skills=listing, dry_run=True)  # no live file written
    assert index == {}  # dry-run writes nothing
    out = capsys.readouterr().out
    assert "WARN  could not read the live index" in out and "1 skill(s) indexed" in out
    assert not (tmp_path / "repo" / "dist").exists()


def test_yanked_skill_versions_become_tombstones(tmp_path, monkeypatch, capsys):
    listing = [{"name": "valid", "repo": _skill_repo(tmp_path / "skill-src"), "ref": "main"}]
    # an unlisted skill's version and the listed skill's own current version
    index = _build(tmp_path, monkeypatch, skills=listing, yanked=["gone@1.2.10", "gone@1.2.9", "valid@1.0.0"])
    assert index["skills"] == [
        {"name": "gone", "version": "1.2.9", "yanked": True, "files": []},
        {"name": "gone", "version": "1.2.10", "yanked": True, "files": []},
        {"name": "valid", "version": "1.0.0", "yanked": True, "files": []},
    ]
    assert "valid@1.0.0 is listed in skills.json but yanked" in capsys.readouterr().out
    assert not (tmp_path / "repo" / "dist" / "skills").exists()
    assert index["templates"][0]["name"] == "dca"  # template tombstones and entries unaffected

    tomb = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())["definitions"]["skillTombstone"]
    for row in index["skills"]:
        assert set(tomb["required"]) == set(row)


def _requiring_skill_repo(root: Path, name: str, requires: list[str]) -> str:
    """The valid fixture renamed `name`, requiring `requires`, as a real local git repo."""
    shutil.copytree(SKILL_FIXTURES / "valid", root)
    md = root / "SKILL.md"
    bins = '"requires":{"bins":["acp","bevo-read","bevo-notify"]'
    md.write_text(md.read_text().replace("name: valid", f"name: {name}", 1)
                  .replace(bins, bins + ',"skills":' + json.dumps(requires), 1))
    git("init", "-q", "-b", "main", cwd=root)
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "init", cwd=root)
    return root.as_uri()


def test_a_skill_row_carries_its_step_budget_and_required_skills(tmp_path, monkeypatch):
    index = _build(tmp_path, monkeypatch, skills=[
        {"name": "requires-skill", "repo": _skill_repo(tmp_path / "dependent", "requires-skill"), "ref": "main"},
        {"name": "valid", "repo": _skill_repo(tmp_path / "base"), "ref": "main"},
    ])
    rows = {row["name"]: row for row in index["skills"]}
    assert rows["requires-skill"]["maxSteps"] == 150
    assert rows["requires-skill"]["requires"] == {"bins": ["bevo-read"], "skills": ["valid"]}
    assert "maxSteps" not in rows["valid"] and rows["valid"]["requires"]["skills"] == []

    skill_schema = json.loads((REPO_ROOT / "schema" / "index.schema.json").read_text())["definitions"]["skill"]
    steps = skill_schema["properties"]["maxSteps"]
    assert steps["minimum"] <= rows["requires-skill"]["maxSteps"] <= steps["maximum"]
    for row in rows.values():
        assert set(skill_schema["required"]) <= set(row) <= set(skill_schema["properties"])
        assert set(row["requires"]) == set(skill_schema["properties"]["requires"]["required"])


def test_a_skill_requiring_one_the_build_does_not_publish_fails_the_build(tmp_path, monkeypatch):
    """A butler installs a required skill first, so the index never serves a row whose
    requirement it does not serve too — and the whole build fails, like an invalid skill."""
    dependent = {"name": "requires-skill", "repo": _skill_repo(tmp_path / "dependent", "requires-skill"), "ref": "main"}
    base = {"name": "valid", "repo": _skill_repo(tmp_path / "base"), "ref": "main"}

    with pytest.raises(SystemExit) as e:  # not listed
        _build(tmp_path / "unlisted", monkeypatch, skills=[dependent])
    assert ("skill requires-skill: ERROR metadata.butler.requires.skills: 'valid' is not listed in skills.json"
            in str(e.value))
    assert "the last deploy stays live" in str(e.value)
    assert not (tmp_path / "unlisted" / "repo" / "dist").exists()

    with pytest.raises(SystemExit) as e:  # listed, but the build publishes only its tombstone
        _build(tmp_path / "yanked", monkeypatch, skills=[dependent, base], yanked=["valid@1.0.0"])
    assert "'valid' is listed in skills.json but this build does not publish it" in str(e.value)

    cycle = [{"name": a, "repo": _requiring_skill_repo(tmp_path / a, a, [b]), "ref": "main"}
             for a, b in (("one", "two"), ("two", "one"))]
    with pytest.raises(SystemExit) as e:
        _build(tmp_path / "cycle", monkeypatch, skills=cycle)
    assert "skill one: ERROR metadata.butler.requires.skills: forms a cycle (one → two → one)" in str(e.value)


def test_yanked_json_splits_by_kind_and_still_refuses_typos():
    assert build_index.split_yanked({"dca@2", "valid@1.0.0", "typo"}) == ({"dca@2", "typo"}, {"valid@1.0.0"})
    with pytest.raises(SystemExit) as e:
        build_index.tombstone_entries({"typo"}, [])
    assert "yanked.json" in str(e.value)
