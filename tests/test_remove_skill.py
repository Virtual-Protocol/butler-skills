"""test_remove_skill.py — pytest coverage for scripts/remove_skill.py.

The script edits the registry files in place, so every test points its
REGISTRY_PATH / SKILLS_REGISTRY_PATH / YANKED_PATH at copies in tmp_path.
What matters is the difference between the two removals: a de-list leaves
yanked.json alone (and a duty already created from the template keeps
running), while --yank writes the tombstone spec — `name@N` for a template,
`name@X.Y.Z` for a skill.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "butler_skills_remove_skill", REPO_ROOT / "scripts" / "remove_skill.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


remove_skill = _load()

REGISTRY = {
    "comment": "the registry",
    "templates": [
        {"name": "tmpl-alpha", "repo": "https://github.com/Virtual-Protocol/butler-skill-alpha", "ref": "main"},
        {"name": "tmpl-beta", "repo": "https://github.com/Virtual-Protocol/butler-skill-beta", "ref": "v1"},
    ],
}


SKILLS = {
    "comment": "the skills",
    "skills": [
        {"name": "skill-alpha", "repo": "https://github.com/Virtual-Protocol/butler-skill-skill-alpha", "ref": "main"},
    ],
}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg, yank = tmp_path / "templates.json", tmp_path / "yanked.json"
    reg.write_text(json.dumps(REGISTRY, indent=2) + "\n")
    yank.write_text(json.dumps({"yanked": []}, indent=2) + "\n")
    skills = tmp_path / "skills.json"
    skills.write_text(json.dumps(SKILLS, indent=2) + "\n")
    monkeypatch.setattr(remove_skill, "REGISTRY_PATH", reg)
    monkeypatch.setattr(remove_skill, "SKILLS_REGISTRY_PATH", skills)
    monkeypatch.setattr(remove_skill, "YANKED_PATH", yank)
    return reg, yank


def run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["remove_skill.py", *argv])
    return remove_skill.main()


def test_delist_drops_the_entry_and_leaves_yanked_alone(registry, monkeypatch):
    reg, yank = registry
    assert run(monkeypatch, "tmpl-alpha") == 0
    data = json.loads(reg.read_text())
    assert [r["name"] for r in data["templates"]] == ["tmpl-beta"]
    assert data["comment"] == REGISTRY["comment"], "the rest of the file survives"
    assert json.loads(yank.read_text()) == {"yanked": []}


def test_yank_writes_the_tombstone_spec(registry, monkeypatch):
    reg, yank = registry
    assert run(monkeypatch, "tmpl-alpha", "--yank", "--version", "2") == 0
    assert [r["name"] for r in json.loads(reg.read_text())["templates"]] == ["tmpl-beta"]
    assert json.loads(yank.read_text())["yanked"] == ["tmpl-alpha@2"]


def test_yank_is_sorted_and_never_duplicates(registry, monkeypatch):
    reg, yank = registry
    yank.write_text(json.dumps({"yanked": ["tmpl-zeta@1", "tmpl-alpha@2"]}, indent=2) + "\n")
    assert run(monkeypatch, "tmpl-alpha", "--yank", "--version", "2") == 0
    assert json.loads(yank.read_text())["yanked"] == ["tmpl-alpha@2", "tmpl-zeta@1"]


def test_dry_run_writes_nothing(registry, monkeypatch):
    reg, yank = registry
    before = (reg.read_text(), yank.read_text())
    assert run(monkeypatch, "tmpl-alpha", "--yank", "--version", "2", "--dry-run") == 0
    assert (reg.read_text(), yank.read_text()) == before


def test_unlisted_template_fails_loudly(registry, monkeypatch):
    reg, _ = registry
    before = reg.read_text()
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, "tmpl-typo")
        assert "not listed" in str(e.value)
    assert reg.read_text() == before


def test_version_requires_yank(registry, monkeypatch):
    with pytest.raises(SystemExit):
        run(monkeypatch, "tmpl-alpha", "--version", "2")


def test_published_version_reads_the_live_entry_not_a_tombstone(monkeypatch, tmp_path):
    """A yanked entry for the same name carries no files[], so the lookup
    must skip it rather than tombstone the tombstone's version."""
    index = {"templates": [
        {"name": "tmpl-alpha", "version": 1, "files": []},
        {"name": "tmpl-alpha", "version": 3, "files": [{"path": "recipe.json"}]},
    ]}
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    assert remove_skill.published_version("tmpl-alpha", path.as_uri()) == 3


def test_published_version_without_an_entry_says_to_pass_version(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"templates": []}))
    with pytest.raises(SystemExit) as e:
        remove_skill.published_version("tmpl-alpha", path.as_uri())
    assert "--version" in str(e.value)


def test_written_files_round_trip_the_checked_in_shape(registry, monkeypatch):
    """2-space indent + trailing newline, so a removal is a clean diff against
    the files as they are checked in."""
    reg, _ = registry
    run(monkeypatch, "tmpl-alpha")
    text = reg.read_text()
    assert text.endswith("}\n") and '\n  "templates": [' in text


# --- skills -----------------------------------------------------------------------------------


def test_a_skill_is_removed_from_skills_json_only(registry, monkeypatch, capsys):
    reg, yank = registry
    skills = reg.parent / "skills.json"
    before = reg.read_text()
    assert run(monkeypatch, "skill-alpha") == 0
    assert json.loads(skills.read_text()) == {"comment": "the skills", "skills": []}
    assert reg.read_text() == before and json.loads(yank.read_text()) == {"yanked": []}
    out = capsys.readouterr().out
    assert "removed skill-alpha from skills.json (0 skill(s) left)" in out
    assert 'git commit -am "skills: remove skill-alpha"' in out


def test_a_skill_yank_is_a_semver_spec(registry, monkeypatch):
    _, yank = registry
    assert run(monkeypatch, "skill-alpha", "--yank", "--version", "1.2.3") == 0
    assert json.loads(yank.read_text())["yanked"] == ["skill-alpha@1.2.3"]


def test_the_version_must_fit_the_kind(registry, monkeypatch):
    with pytest.raises(SystemExit):
        run(monkeypatch, "skill-alpha", "--yank", "--version", "2")
    with pytest.raises(SystemExit):
        run(monkeypatch, "tmpl-alpha", "--yank", "--version", "1.0.0")


def test_published_version_of_a_skill_skips_tombstones(tmp_path):
    index = {"templates": [{"name": "skill-alpha", "version": 9, "files": [{"path": "recipe.json"}]}], "skills": [
        {"name": "skill-alpha", "version": "1.0.0", "yanked": True, "files": []},
        {"name": "skill-alpha", "version": "1.1.0", "files": [{"path": "SKILL.md"}]},
    ]}
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    assert remove_skill.published_version("skill-alpha", path.as_uri(), kind="skill") == "1.1.0"
    path.write_text(json.dumps({"skills": []}))
    with pytest.raises(SystemExit) as e:
        remove_skill.published_version("skill-alpha", path.as_uri(), kind="skill")
    assert "--version <X.Y.Z>" in str(e.value)
