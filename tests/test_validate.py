"""test_validate.py — pytest coverage for scripts/validate.py against the
fixture templates under tests/fixtures/templates/{valid, missing-key,
dynamic-argv, retired-sdk, undeclared-env, oversize-description, no-waiter,
bad-params}, plus the git-backed rules: --standalone mode (id from
recipe.json), the tree rules (symlinks, nested submodules/repos, the 50-file
and 1 MB caps) and registry mode (the checkout is named after its
templates.json entry; `--all` clones every entry).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "templates"


def _load_validate_module():
    spec = importlib.util.spec_from_file_location("butler_skills_validate", REPO_ROOT / "scripts" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


validate = _load_validate_module()


def run(name: str, maintainer: bool = False, standalone: bool = True):
    reserved = validate.load_reserved()
    template_dir = FIXTURES / name
    ok, result = validate.validate_template(template_dir, reserved, maintainer, json_mode=True, standalone=standalone)
    return ok, result


def test_valid_template_passes():
    ok, result = run("valid")
    assert ok, result["errors"]
    assert result["errors"] == []
    assert result["warnings"] == []


def test_missing_idempotency_key_fails():
    ok, result = run("missing-key")
    assert not ok
    assert any("idempotency-key" in e for e in result["errors"])


def test_dynamic_argv_warns_but_does_not_refuse():
    ok, result = run("dynamic-argv")
    assert ok, result["errors"]
    assert any("partly dynamic" in w for w in result["warnings"])


def test_retired_sdk_call_fails_with_the_replacement_named():
    ok, result = run("retired-sdk")
    assert not ok
    assert any("bevo.trade(...) is retired" in e and "acp trade" in e for e in result["errors"])


def test_undeclared_env_key_fails():
    ok, result = run("undeclared-env")
    assert not ok
    assert any("SECRET_PARAM" in e for e in result["errors"])


def test_oversize_description_fails():
    ok, result = run("oversize-description")
    assert not ok
    assert any(e.startswith("description") for e in result["errors"])


def test_no_waiter_fails():
    ok, result = run("no-waiter")
    assert not ok
    assert any("never calls a waiter" in e for e in result["errors"])


def test_bad_params_schema_fails():
    ok, result = run("bad-params")
    assert not ok
    assert any("unsupported JSON-Schema keyword" in e for e in result["errors"])


def test_reserved_id_rejected(tmp_path):
    reserved = {"web-checkout"}
    _write_minimal_template(tmp_path / "web-checkout", "web-checkout")
    ok, result = validate.validate_template(tmp_path / "web-checkout", reserved, maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("reserved" in e for e in result["errors"])


def test_butler_prefix_requires_maintainer(tmp_path):
    reserved: set[str] = set()
    template_dir = tmp_path / "butler-new-thing"
    _write_minimal_template(template_dir, "butler-new-thing")
    ok, result = validate.validate_template(template_dir, reserved, maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("maintainer-only 'butler-' prefix" in e for e in result["errors"])
    ok2, _ = validate.validate_template(template_dir, reserved, maintainer=True, json_mode=True, standalone=True)
    assert ok2


def test_bevo_prefix_is_refused_even_for_maintainers(tmp_path):
    template_dir = tmp_path / "bevo-new-thing"
    _write_minimal_template(template_dir, "bevo-new-thing")
    for maintainer in (False, True):
        ok, result = validate.validate_template(template_dir, set(), maintainer=maintainer, json_mode=True, standalone=True)
        assert not ok
        assert any(e.startswith("id:") and "bundled-command" in e for e in result["errors"]), result["errors"]
        assert not any("maintainer-only" in e for e in result["errors"])


def test_embedded_reserved_list_matches_schema_json():
    on_disk = set(json.loads((REPO_ROOT / "schema" / "reserved-names.json").read_text())["reserved"])
    assert set(validate.RESERVED_NAMES_BUILTIN) == on_disk
    assert validate.load_reserved() == on_disk


def test_load_reserved_falls_back_to_embedded_list_without_schema_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "RESERVED_PATH", tmp_path / "does-not-exist.json")
    assert validate.load_reserved() == set(validate.RESERVED_NAMES_BUILTIN)


def test_downloaded_tooling_in_the_tree_is_a_warning_not_an_error(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "validate.py").write_text("# downloaded\n")
    (template_dir / "replay.py").write_text("# downloaded\n")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]
    assert any("validate.py looks like downloaded hub tooling" in w for w in result["warnings"])
    assert any("replay.py looks like downloaded hub tooling" in w for w in result["warnings"])


# --- git-backed registry: --standalone mode, tree rules, layout rules --------------------


def _write_minimal_template(template_dir: Path, tid: str, triggers=None) -> None:
    template_dir.mkdir(parents=True, exist_ok=True)
    recipe = {
        "id": tid,
        "version": 1,
        "description": "A minimal fixture template.",
        "params": {"type": "object", "properties": {}},
    }
    if triggers is not None:
        recipe["triggers"] = triggers
    (template_dir / "recipe.json").write_text(json.dumps(recipe))
    (template_dir / "duty.py").write_text("import bevo\nbevo.log('x')\n")
    (template_dir / "README.md").write_text("# fixture\n")


def test_standalone_takes_id_from_recipe_json_not_directory(tmp_path):
    template_dir = tmp_path / "butler-skill-foo-checkout"
    _write_minimal_template(template_dir, "foo")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]
    assert result["template"] == "foo"

    # Registry mode on the same directory still enforces id == directory.
    ok2, result2 = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert not ok2
    assert any(e.startswith("id:") and "must equal directory name" in e for e in result2["errors"])


def test_standalone_still_requires_a_valid_template_id(tmp_path):
    template_dir = tmp_path / "anything"
    _write_minimal_template(template_dir, "_template")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("id:") and "must match" in e for e in result["errors"])


def test_standalone_ignores_the_authors_git_dir_and_pycache(tmp_path):
    template_dir = tmp_path / "repo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / ".git").mkdir()
    (template_dir / ".git" / "big.pack").write_bytes(b"\0" * (validate.MAX_TREE_BYTES + 1))
    (template_dir / "__pycache__").mkdir()
    (template_dir / "__pycache__" / "duty.cpython-311.pyc").write_bytes(b"\0" * 10)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]


def test_symlink_anywhere_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "docs").mkdir()
    os.symlink(template_dir / "recipe.json", template_dir / "docs" / "link.json")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("layout:") and "symlink" in e and "docs/link.json" in e for e in result["errors"])


def test_symlinked_directory_is_refused_and_not_followed(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("x")
    os.symlink(outside, template_dir / "vendor", target_is_directory=True)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("layout:") and "symlink" in e and "vendor" in e for e in result["errors"])


def test_nested_gitmodules_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = https://github.com/a/b\n')
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("layout:") and "nested submodules" in e for e in result["errors"])


def test_nested_git_repository_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "vendor" / ".git").mkdir(parents=True)
    (template_dir / "vendor" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("layout:") and "nested git repository" in e and "vendor/.git" in e for e in result["errors"])


def test_more_than_50_files_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "fixtures").mkdir()
    for i in range(validate.MAX_TREE_FILES):
        (template_dir / "fixtures" / f"f{i}.json").write_text("{}")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("layout:") and f"must be <= {validate.MAX_TREE_FILES}" in e for e in result["errors"])


def test_more_than_1mb_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "notes.md").write_bytes(b"x" * (validate.MAX_TREE_BYTES + 1))
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("layout:") and f"must be <= {validate.MAX_TREE_BYTES}" in e for e in result["errors"])


def test_missing_required_file_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "README.md").unlink()
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("missing required file: README.md" in e for e in result["errors"])


def test_registry_mode_requires_the_recipe_id_to_match_the_registry_name(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "bar")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert not ok
    assert any(e.startswith("id:") and "must equal directory name" in e for e in result["errors"])
    ok2, _ = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok2


def test_triggers_outside_the_three_kinds_is_refused(tmp_path):
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo", triggers=["timer", "price"])
    (template_dir / "duty.py").write_text("import bevo\nfor t in bevo.ticks():\n    bevo.log(t)\n")
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("unknown trigger kind" in e and "price" in e for e in result["errors"])


def test_load_registry_reads_templates_json(tmp_path):
    registry = tmp_path / "templates.json"
    rows = [{"name": "foo", "repo": "https://github.com/someone/butler-skill-foo", "ref": "main"}]
    registry.write_text(json.dumps({"templates": rows}))
    assert validate.load_registry(registry) == rows

    # Empty is a legitimate registry — day one, or every template yanked — so
    # it reads as an empty list rather than an error. A MISSING key is still a
    # malformed file, and so is a missing registry.
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"templates": []}))
    assert validate.load_registry(empty) == []

    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"skills": []}))
    with pytest.raises(SystemExit):
        validate.load_registry(malformed)
    with pytest.raises(SystemExit):
        validate.load_registry(tmp_path / "missing.json")


def _make_template_repo(root: Path, tid: str) -> str:
    """A real local git repo holding one template, so clone_registry_templates
    has something to clone without touching the network."""
    _write_minimal_template(root, tid, triggers=[])
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=str(root), check=True, capture_output=True, env=env)
    return root.as_uri()


def test_all_clones_every_registry_entry_into_a_directory_named_for_it(tmp_path, monkeypatch):
    registry = tmp_path / "templates.json"
    registry.write_text(json.dumps({"templates": [
        {"name": "foo", "repo": _make_template_repo(tmp_path / "src-foo", "foo"), "ref": "main"},
        {"name": "bar", "repo": _make_template_repo(tmp_path / "src-bar", "bar"), "ref": "main"},
    ]}))
    monkeypatch.setattr(validate, "REGISTRY_PATH", registry)

    work = tmp_path / "work"
    work.mkdir()
    dirs = validate.clone_registry_templates(work)
    assert [d.name for d in dirs] == ["bar", "foo"]  # sorted
    for d in dirs:
        assert (d / "recipe.json").exists()
        ok, result = validate.validate_template(d, set(), maintainer=False, json_mode=True, standalone=False)
        assert ok, (d, result["errors"])


def test_clone_registry_templates_fails_loudly_on_a_ref_that_does_not_resolve(tmp_path, monkeypatch):
    registry = tmp_path / "templates.json"
    registry.write_text(json.dumps({"templates": [
        {"name": "foo", "repo": _make_template_repo(tmp_path / "src-foo", "foo"), "ref": "no-such-ref"},
    ]}))
    monkeypatch.setattr(validate, "REGISTRY_PATH", registry)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit) as e:
        validate.clone_registry_templates(work)
    assert "no-such-ref" in str(e.value)


def test_the_local_valid_fixture_passes_in_both_modes(template_checkout):
    reserved = validate.load_reserved()
    ok, result = validate.validate_template(template_checkout, reserved, maintainer=True, json_mode=True)
    assert ok, result["errors"]
    ok2, result2 = validate.validate_template(template_checkout, reserved, maintainer=True, json_mode=True, standalone=True)
    assert ok2, result2["errors"]
    assert result2["template"] == "valid"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# --- README.md is written for the model, not for a human --------------------------------
#
# `recipe_show` hands this file to Butler verbatim on every call, so its cost is
# context on a container that already carries a large standing prompt — and its
# audience is a reader that was explicitly told to treat it as data.


def _readme_issues(tmp_path: Path, body: str) -> list[str]:
    """Validate a minimal template carrying `body` as its README; return warnings."""
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "README.md").write_text(body)
    ok, result = validate.validate_template(
        template_dir, set(), maintainer=False, json_mode=True, standalone=True
    )
    assert ok, result["errors"]
    return result["warnings"]


def test_readme_accepts_a_description_of_the_program(tmp_path):
    clean = (
        "Buys a fixed dollar amount of one token on a schedule.\n\n"
        "It will not check the price, and it will not buy twice for one slot.\n"
    )
    assert _readme_issues(tmp_path, clean) == []


def test_readme_warns_on_human_repository_furniture(tmp_path):
    warnings = _readme_issues(
        tmp_path,
        # Allowlisted URLs throughout, so this isolates the furniture check from
        # the separate url-lint rule.
        "# butler-skill-foo\n\n"
        "[![build](https://github.com/Virtual-Protocol/butler-skill-foo/badge.svg)]"
        "(https://github.com/Virtual-Protocol/butler-skill-foo)\n\n"
        "## Installation\n\ngit clone https://github.com/Virtual-Protocol/butler-skill-foo\n\n"
        "## License\n\nMIT\n",
    )
    joined = " ".join(warnings)
    assert "badge" in joined
    assert "install section" in joined
    assert "clone/install command" in joined
    assert "licence/contributing/changelog" in joined


def test_readme_warns_when_it_instructs_the_reader(tmp_path):
    # The container fences this file as data, so an imperative is ignored by
    # design — wasted context rather than a working instruction.
    warnings = _readme_issues(tmp_path, "First, ask the owner which token they want.\n")
    assert any("DATA, not instructions" in w for w in warnings)


def test_readme_does_not_warn_on_a_heading_that_starts_with_a_verb(tmp_path):
    # Only the first prose line is judged, and headings are skipped: "## Running
    # costs" is a section title, not an instruction.
    assert _readme_issues(tmp_path, "## Running costs\n\nOne model call per fire.\n") == []


def test_readme_warns_then_refuses_on_size(tmp_path):
    body = "Buys a token.\n" + ("x" * (validate.README_WARN_BYTES + 10))
    assert any("prefilled" in w for w in _readme_issues(tmp_path, body))

    template_dir = tmp_path / "big"
    _write_minimal_template(template_dir, "big")
    (template_dir / "README.md").write_text("Buys a token.\n" + "x" * (validate.README_MAX_BYTES + 10))
    ok, result = validate.validate_template(
        template_dir, set(), maintainer=False, json_mode=True, standalone=True
    )
    assert not ok
    assert any("README.md" in e for e in result["errors"])
