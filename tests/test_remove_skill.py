"""test_remove_skill.py — pytest coverage for scripts/remove_skill.py.

The script edits the two registry files in place, so every test points its
REGISTRY_PATH / YANKED_PATH at copies in tmp_path. What matters is the
difference between the two removals: a de-list leaves yanked.json alone (and
installed copies enabled), while --yank writes the tombstone spec that is the
only thing which disables them.
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
    "skills": [
        {"name": "butler-alpha", "repo": "https://github.com/Virtual-Protocol/butler-skill-alpha", "ref": "main"},
        {"name": "butler-beta", "repo": "https://github.com/Virtual-Protocol/butler-skill-beta", "ref": "v1.0.0"},
    ],
}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg, yank = tmp_path / "skills.json", tmp_path / "yanked.json"
    reg.write_text(json.dumps(REGISTRY, indent=2) + "\n")
    yank.write_text(json.dumps({"yanked": []}, indent=2) + "\n")
    monkeypatch.setattr(remove_skill, "REGISTRY_PATH", reg)
    monkeypatch.setattr(remove_skill, "YANKED_PATH", yank)
    return reg, yank


def run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["remove_skill.py", *argv])
    return remove_skill.main()


def test_delist_drops_the_entry_and_leaves_yanked_alone(registry, monkeypatch):
    """The default removal: out of the index, but no tombstone — so a butler
    that already has it keeps it, enabled."""
    reg, yank = registry
    assert run(monkeypatch, "butler-alpha") == 0
    data = json.loads(reg.read_text())
    assert [r["name"] for r in data["skills"]] == ["butler-beta"]
    assert data["comment"] == REGISTRY["comment"], "the rest of the file survives"
    assert json.loads(yank.read_text()) == {"yanked": []}


def test_yank_writes_the_tombstone_spec(registry, monkeypatch):
    """--yank is what disables installed copies; it must record name@version."""
    reg, yank = registry
    assert run(monkeypatch, "butler-alpha", "--yank", "--version", "2.1.0") == 0
    assert [r["name"] for r in json.loads(reg.read_text())["skills"]] == ["butler-beta"]
    assert json.loads(yank.read_text())["yanked"] == ["butler-alpha@2.1.0"]


def test_yank_is_sorted_and_never_duplicates(registry, monkeypatch):
    reg, yank = registry
    yank.write_text(json.dumps({"yanked": ["butler-zeta@1.0.0", "butler-alpha@2.1.0"]}, indent=2) + "\n")
    assert run(monkeypatch, "butler-alpha", "--yank", "--version", "2.1.0") == 0
    assert json.loads(yank.read_text())["yanked"] == ["butler-alpha@2.1.0", "butler-zeta@1.0.0"]


def test_dry_run_writes_nothing(registry, monkeypatch):
    reg, yank = registry
    before = (reg.read_text(), yank.read_text())
    assert run(monkeypatch, "butler-alpha", "--yank", "--version", "2.1.0", "--dry-run") == 0
    assert (reg.read_text(), yank.read_text()) == before


def test_unlisted_skill_fails_loudly(registry, monkeypatch):
    """A typo must not silently rewrite the registry with nothing removed."""
    reg, _ = registry
    before = reg.read_text()
    with pytest.raises(SystemExit) as e:
        run(monkeypatch, "butler-typo")
        assert "not listed" in str(e.value)
    assert reg.read_text() == before


def test_version_requires_yank(registry, monkeypatch):
    with pytest.raises(SystemExit):
        run(monkeypatch, "butler-alpha", "--version", "2.1.0")


def test_bad_version_is_refused(registry, monkeypatch):
    for bad in ("2.1", "v2.1.0", "latest"):
        with pytest.raises(SystemExit):
            run(monkeypatch, "butler-alpha", "--yank", "--version", bad)


def test_published_version_reads_the_live_entry_not_a_tombstone(monkeypatch, tmp_path):
    """A yanked entry for the same name carries no installable version, so the
    lookup must skip it rather than tombstone the tombstone's version."""
    index = {"skills": [
        {"name": "butler-alpha", "version": "1.0.0", "yanked": True},
        {"name": "butler-alpha", "version": "3.2.1"},
    ]}
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    assert remove_skill.published_version("butler-alpha", path.as_uri()) == "3.2.1"


def test_published_version_without_an_entry_says_to_pass_version(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"skills": []}))
    with pytest.raises(SystemExit) as e:
        remove_skill.published_version("butler-alpha", path.as_uri())
    assert "--version" in str(e.value)


def test_written_files_round_trip_the_checked_in_shape(registry, monkeypatch):
    """2-space indent + trailing newline, so a removal is a clean diff against
    the files as they are checked in."""
    reg, _ = registry
    run(monkeypatch, "butler-alpha")
    text = reg.read_text()
    assert text.endswith("}\n") and '\n  "skills": [' in text
