"""test_validate.py — pytest coverage for scripts/validate.py against the
fixture skills under tests/fixtures/skills/{valid, missing-key,
bad-frontmatter, oversize-description, banned-command, undeclared-param,
unmarked-step}, plus the git-backed rules: --standalone mode (name from the
frontmatter), the tree rules (symlinks, nested submodules/repos, the 50-file
and 1 MB caps) and the registry pin rules (.gitmodules entry, https URL).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "skills"


def _load_validate_module():
    spec = importlib.util.spec_from_file_location("butler_skills_validate", REPO_ROOT / "scripts" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


validate = _load_validate_module()


def run(name: str, maintainer: bool = False):
    reserved = validate.load_reserved()
    skill_dir = FIXTURES / name
    ok, result = validate.validate_skill(skill_dir, reserved, maintainer, json_mode=True)
    return ok, result


def test_valid_skill_passes():
    ok, result = run("valid")
    assert ok, result["errors"]
    assert result["errors"] == []


def test_missing_idempotency_key_fails():
    ok, result = run("missing-key")
    assert not ok
    assert any("idempotency_key" in e for e in result["errors"])


def test_bad_frontmatter_fails():
    ok, result = run("bad-frontmatter")
    assert not ok
    assert any(e.startswith("metadata") for e in result["errors"])


def test_oversize_description_fails():
    ok, result = run("oversize-description")
    assert not ok
    assert any(e.startswith("description") for e in result["errors"])


def test_banned_command_fails():
    ok, result = run("banned-command")
    assert not ok
    assert any(e.startswith("command-allowlist") for e in result["errors"])
    assert any("curl" in e for e in result["errors"])
    assert any("--help" in e for e in result["errors"])


def test_undeclared_param_fails():
    ok, result = run("undeclared-param")
    assert not ok
    assert any("SECRET_PARAM" in e for e in result["errors"])


def test_unmarked_step_fails():
    ok, result = run("unmarked-step")
    assert not ok
    assert any(e.startswith("steps") for e in result["errors"])


def test_reserved_name_rejected(tmp_path):
    reserved = {"bevo-onchain"}
    skill_dir = tmp_path / "bevo-onchain"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: bevo-onchain\ndescription: x\nversion: 1.0.0\n'
        'metadata: {"bevo":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n"
        "## One-off procedure\n1. [FIXED] x\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill_dir / "CHANGELOG.md").write_text("# Changelog\n")
    ok, result = validate.validate_skill(skill_dir, reserved, maintainer=False, json_mode=True)
    assert not ok
    assert any("reserved" in e for e in result["errors"])


def test_bevo_prefix_requires_maintainer(tmp_path):
    reserved: set[str] = set()
    skill_dir = tmp_path / "bevo-new-thing"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: bevo-new-thing\ndescription: x\nversion: 1.0.0\n'
        'metadata: {"bevo":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n"
        "## One-off procedure\n1. [FIXED] x\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill_dir / "CHANGELOG.md").write_text("# Changelog\n")
    ok, _ = validate.validate_skill(skill_dir, reserved, maintainer=False, json_mode=True)
    assert not ok
    ok2, _ = validate.validate_skill(skill_dir, reserved, maintainer=True, json_mode=True)
    assert ok2


# --- git-backed registry: --standalone mode, tree rules, pin rules -----------------


def _write_minimal_skill(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: x\nversion: 1.0.0\n'
        'metadata: {"bevo":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n"
        "## One-off procedure\n1. [FIXED] x\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill_dir / "CHANGELOG.md").write_text("# Changelog\n")


def test_standalone_takes_name_from_frontmatter_not_directory(tmp_path):
    # An author's clone can be called anything (butler-skill-foo, my-checkout, ...).
    skill_dir = tmp_path / "butler-skill-foo-checkout"
    _write_minimal_skill(skill_dir, "foo")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]
    assert result["skill"] == "foo"
    assert result["promptCost"] == validate.prompt_cost("foo", "x", "skills/foo/SKILL.md")

    # Registry mode on the same directory still enforces name == directory.
    ok2, result2 = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert not ok2
    assert any(e.startswith("name:") and "must equal directory name" in e for e in result2["errors"])


def test_standalone_still_requires_a_valid_skill_name(tmp_path):
    skill_dir = tmp_path / "anything"
    _write_minimal_skill(skill_dir, "_template")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("name:") and "must match" in e for e in result["errors"])


def test_standalone_ignores_the_authors_git_dir_and_pycache(tmp_path):
    skill_dir = tmp_path / "repo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / ".git").mkdir()
    (skill_dir / ".git" / "big.pack").write_bytes(b"\0" * (validate.MAX_TREE_BYTES + 1))
    (skill_dir / "__pycache__").mkdir()
    (skill_dir / "__pycache__" / "duty.cpython-311.pyc").write_bytes(b"\0" * 10)
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]


def test_symlink_anywhere_is_refused(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / "docs").mkdir()
    os.symlink(skill_dir / "SKILL.md", skill_dir / "docs" / "link.md")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("tree:") and "symlink" in e and "docs/link.md" in e for e in result["errors"])


def test_symlinked_directory_is_refused_and_not_followed(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("x")
    os.symlink(outside, skill_dir / "vendor", target_is_directory=True)
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("tree:") and "symlink" in e and "vendor" in e for e in result["errors"])


def test_nested_gitmodules_is_refused(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = https://github.com/a/b\n')
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("tree:") and "nested submodules" in e for e in result["errors"])


def test_nested_git_repository_is_refused(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / "vendor" / ".git").mkdir(parents=True)
    (skill_dir / "vendor" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("tree:") and "nested git repository" in e and "vendor/.git" in e for e in result["errors"])


def test_more_than_50_files_is_refused(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / "fixtures").mkdir()
    for i in range(validate.MAX_TREE_FILES):  # 2 skill files + 50 = 52 > 50
        (skill_dir / "fixtures" / f"f{i}.json").write_text("{}")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("tree:") and f"must be <= {validate.MAX_TREE_FILES}" in e for e in result["errors"])


def test_more_than_1mb_is_refused(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / "notes.md").write_bytes(b"x" * (validate.MAX_TREE_BYTES + 1))
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("tree:") and f"must be <= {validate.MAX_TREE_BYTES}" in e for e in result["errors"])
    assert any(e.startswith("bundle-size:") for e in result["errors"])  # the older 200 KB rule still fires too


def test_registry_mode_refuses_a_vendored_directory_under_skills(tmp_path, monkeypatch):
    # A plain directory under skills/ that is not declared in .gitmodules.
    root = tmp_path / "registry"
    (root / "skills").mkdir(parents=True)
    (root / ".gitmodules").write_text("")
    monkeypatch.setattr(validate, "REPO_ROOT", root)
    monkeypatch.setattr(validate, "SKILLS_DIR", root / "skills")
    monkeypatch.setattr(validate, "GITMODULES_PATH", root / ".gitmodules")
    skill_dir = root / "skills" / "foo"
    _write_minimal_skill(skill_dir, "foo")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert not ok
    assert any(e.startswith("submodule:") and "not declared in .gitmodules" in e for e in result["errors"])


def test_registry_mode_refuses_non_https_github_submodule_url(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    (root / "skills").mkdir(parents=True)
    (root / ".gitmodules").write_text(
        '[submodule "skills/foo"]\n\tpath = skills/foo\n\turl = git@github.com:someone/butler-skill-foo.git\n'
    )
    monkeypatch.setattr(validate, "REPO_ROOT", root)
    monkeypatch.setattr(validate, "SKILLS_DIR", root / "skills")
    monkeypatch.setattr(validate, "GITMODULES_PATH", root / ".gitmodules")
    skill_dir = root / "skills" / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / ".git").write_text("gitdir: ../../.git/modules/skills/foo\n")  # an initialised submodule's gitlink
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert not ok
    assert any(e.startswith("submodule:") and "https://github.com" in e for e in result["errors"])


def test_registry_mode_accepts_declared_https_submodule(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    (root / "skills").mkdir(parents=True)
    (root / ".gitmodules").write_text(
        '[submodule "skills/foo"]\n\tpath = skills/foo\n\turl = https://github.com/someone/butler-skill-foo\n'
    )
    monkeypatch.setattr(validate, "REPO_ROOT", root)
    monkeypatch.setattr(validate, "SKILLS_DIR", root / "skills")
    monkeypatch.setattr(validate, "GITMODULES_PATH", root / ".gitmodules")
    skill_dir = root / "skills" / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / ".git").write_text("gitdir: ../../.git/modules/skills/foo\n")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert ok, result["errors"]
    assert validate.registry_skill_dirs() == [skill_dir]


def test_real_registry_submodules_pass_in_both_modes():
    reserved = validate.load_reserved()
    dirs = validate.registry_skill_dirs()
    assert dirs, "no skills/<name> submodules declared in .gitmodules"
    for d in dirs:
        assert (d / "SKILL.md").exists(), f"{d} is not initialised — run git submodule update --init --recursive"
        ok, result = validate.validate_skill(d, reserved, maintainer=True, json_mode=True)
        assert ok, (d, result["errors"])
        ok2, result2 = validate.validate_skill(d, reserved, maintainer=True, json_mode=True, standalone=True)
        assert ok2, (d, result2["errors"])
        assert result2["skill"] == d.name


def test_parse_gitmodules_reads_git_style_indented_keys(tmp_path):
    gm = tmp_path / ".gitmodules"
    gm.write_text(
        '[submodule "skills/a"]\n\tpath = skills/a\n\turl = https://github.com/x/a\n'
        '[submodule "skills/b"]\n    path = skills/b\n    url = https://github.com/x/b.git\n'
    )
    entries = validate.parse_gitmodules(gm)
    assert set(entries) == {"skills/a", "skills/b"}
    assert entries["skills/a"]["url"] == "https://github.com/x/a"
    assert entries["skills/b"]["name"] == "skills/b"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
