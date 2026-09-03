"""test_validate.py — pytest coverage for scripts/validate.py against the
fixture skills under tests/fixtures/skills/{valid, missing-key,
bad-frontmatter, oversize-description, banned-command, undeclared-param,
unmarked-step}.
"""
from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
