"""test_validate.py — pytest coverage for scripts/validate.py against the
fixture templates under tests/fixtures/templates/{valid, missing-key,
dynamic-argv, retired-sdk, undeclared-env, oversize-description, no-waiter,
bad-params}, plus the git-backed rules: --standalone mode (id from
recipe.json), the tree rules (symlinks, nested submodules/repos, the 50-file
and 1 MB caps) and registry mode (the checkout is named after its
templates.json entry; `--all` clones every entry).

The second half covers the skill kind (SKILL.md) against
tests/fixtures/skills/{valid, yaml-colon, openclaw-meta, unknown-cmd,
retired-refs, money-unfixed, bins-undeclared} and skills written in tmp_path.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
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


# --- params.allOf (root-only conditional-required) -----------------------------------------


def _write_params_template(tmp_path, tid: str, params: dict):
    template_dir = tmp_path / tid
    template_dir.mkdir(parents=True, exist_ok=True)
    recipe = {
        "id": tid,
        "version": 1,
        "description": "A conditional-params fixture template.",
        "params": params,
    }
    (template_dir / "recipe.json").write_text(json.dumps(recipe))
    (template_dir / "duty.py").write_text("import bevo\nbevo.log('x')\n")
    (template_dir / "README.md").write_text("# fixture\n")
    return template_dir


_VALID_ALLOF_PARAMS = {
    "type": "object",
    "properties": {
        "SIZING": {"type": "string", "enum": ["fixed", "cash_share", "leader_share"]},
        "SIZE_USD": {"type": "number", "minimum": 1},
        "SHARE": {"type": "number", "minimum": 0.01, "maximum": 1},
    },
    "required": ["SIZING"],
    "allOf": [
        {"if": {"properties": {"SIZING": {"const": "fixed"}}}, "then": {"required": ["SIZE_USD"]}},
        {
            "if": {"properties": {"SIZING": {"enum": ["cash_share", "leader_share"]}}},
            "then": {"required": ["SHARE"]},
        },
    ],
}


def test_valid_allof_conditional_required_passes(tmp_path):
    template_dir = _write_params_template(tmp_path, "valid-allof", _VALID_ALLOF_PARAMS)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]
    assert not any(e.startswith("params") for e in result["errors"])


def test_allof_nested_under_a_property_is_refused(tmp_path):
    params = {
        "type": "object",
        "properties": {
            "SIZING": {
                "type": "string",
                "allOf": [{"if": {"properties": {}}, "then": {"required": []}}],
            }
        },
    }
    template_dir = _write_params_template(tmp_path, "nested-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("unsupported JSON-Schema keyword" in e and "allOf" in e for e in result["errors"])


def test_allof_clause_with_extra_key_is_refused(tmp_path):
    import copy

    params = copy.deepcopy(_VALID_ALLOF_PARAMS)
    params["allOf"][0]["else"] = {"required": []}
    template_dir = _write_params_template(tmp_path, "extra-key-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("params.allOf[0]: unsupported key" in e for e in result["errors"])


def test_allof_if_condition_using_minimum_is_refused(tmp_path):
    import copy

    params = copy.deepcopy(_VALID_ALLOF_PARAMS)
    params["allOf"][0]["if"]["properties"]["SIZING"] = {"minimum": 1}
    template_dir = _write_params_template(tmp_path, "minimum-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("must be an object with exactly one key, 'const' or 'enum'" in e for e in result["errors"])


def test_allof_then_with_anything_but_required_is_refused(tmp_path):
    import copy

    params = copy.deepcopy(_VALID_ALLOF_PARAMS)
    params["allOf"][0]["then"] = {"default": {"SIZE_USD": 5}}
    template_dir = _write_params_template(tmp_path, "bad-then-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("params.allOf[0].then: must be an object with exactly the key 'required'" in e for e in result["errors"])


def test_allof_undeclared_required_name_is_refused(tmp_path):
    import copy

    params = copy.deepcopy(_VALID_ALLOF_PARAMS)
    params["allOf"][0]["then"]["required"] = ["NOT_DECLARED"]
    template_dir = _write_params_template(tmp_path, "undeclared-required-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(
        "params.allOf[0].then.required: 'NOT_DECLARED' is not declared" in e for e in result["errors"]
    )


def test_allof_undeclared_condition_name_is_refused(tmp_path):
    import copy

    params = copy.deepcopy(_VALID_ALLOF_PARAMS)
    params["allOf"][0]["if"]["properties"] = {"NOT_DECLARED": {"const": "fixed"}}
    template_dir = _write_params_template(tmp_path, "undeclared-condition-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(
        "params.allOf[0].if.properties.NOT_DECLARED" in e and "is not declared" in e for e in result["errors"]
    )


def test_allof_const_outside_property_enum_is_refused(tmp_path):
    import copy

    params = copy.deepcopy(_VALID_ALLOF_PARAMS)
    params["allOf"][0]["if"]["properties"]["SIZING"] = {"const": "not-a-valid-option"}
    template_dir = _write_params_template(tmp_path, "bad-const-allof", params)
    ok, result = validate.validate_template(template_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any("not in 'SIZING'" in e and "own enum" in e for e in result["errors"])


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


def test_re_compile_is_not_the_builtin_compile(tmp_path):
    """`re.compile(...)` is an attribute call on an allowed module.

    Reading it as the builtin `compile` refused both shipped templates, each of
    which precompiles an address pattern at module level — the exact templates
    this registry exists to serve. Only a bare call is the builtin.
    """
    template_dir = tmp_path / "foo"
    _write_minimal_template(template_dir, "foo")
    (template_dir / "duty.py").write_text(
        "import bevo\nimport re\n"
        'EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")\n'
        "bevo.log(str(EVM))\n"
    )
    ok, result = validate.validate_template(
        template_dir, set(), maintainer=False, json_mode=True, standalone=True
    )
    assert ok, result["errors"]

    # The bare builtin is still refused.
    (template_dir / "duty.py").write_text("import bevo\nx = compile('1', '<s>', 'eval')\nbevo.log(str(x))\n")
    ok, result = validate.validate_template(
        template_dir, set(), maintainer=False, json_mode=True, standalone=True
    )
    assert not ok
    assert any("compile" in e for e in result["errors"])


# === skills: SKILL.md ======================================================================
#
# A skill is the hub's second kind. Mastra drops a SKILL.md it cannot parse without a word,
# so most of what follows is "would Mastra still see this skill, and does it only run what
# the container actually has".

SKILL_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "skills"
VALID_SKILL_BODY = (SKILL_FIXTURES / "valid" / "SKILL.md").read_text().split("---\n", 2)[2]
VALID_SKILL_FM = {
    "name": "foo",
    "description": "A skill fixture that buys one token once.",
    "version": "1.0.0",
    "metadata": '{"butler":{"moneyMoving":true,"keywords":["fixture"],"requires":{"bins":["acp","bevo-read","bevo-notify"]}}}',
}


def run_skill(name: str, standalone: bool = False):
    """A fixture skill, in registry mode by default (its directory is its name)."""
    return validate.validate_skill(
        SKILL_FIXTURES / name, validate.load_reserved(), False, json_mode=True, standalone=standalone
    )


def write_skill(root: Path, name: str = "foo", fm: dict | None = None, body: str | None = None,
                extra_fm_lines=(), changelog: str = "## 1.0.0\n\n- First.\n") -> Path:
    """A skill repo in `root/name`: the valid fixture's body under a frontmatter built
    from VALID_SKILL_FM plus `fm` (a None value drops that key)."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fields = {**VALID_SKILL_FM, "name": name, **(fm or {})}
    lines = ["---", *(f"{k}: {v}" for k, v in fields.items() if v is not None), *extra_fm_lines, "---"]
    (d / "SKILL.md").write_text("\n".join(lines) + "\n" + (VALID_SKILL_BODY if body is None else body))
    (d / "README.md").write_text(f"# {name}\n")
    (d / "CHANGELOG.md").write_text("# Changelog\n\n" + changelog)
    return d


def check_skill(d: Path, maintainer: bool = False, standalone: bool = True, reserved: set[str] | None = None):
    return validate.validate_skill(d, set() if reserved is None else reserved, maintainer, json_mode=True, standalone=standalone)


def procedure_body(steps: str, moneyish: bool = True) -> str:
    """A full seven-section body whose `## Procedure` is `steps`."""
    idem = "One key per request — do not re-run a money command on an error.\n" if moneyish else "Reads are safe.\n"
    return (
        "## When to use\n\nx\n\n## Before you start\n\nx\n\n## Procedure\n\n" + steps +
        "\n## Idempotency and retries\n\n" + idem +
        "\n## Failure handling\n\nx\n\n## Limits\n\nx\n\n## Say to the owner\n\nx\n"
    )


# --- the fixtures ---------------------------------------------------------------------------


def test_valid_skill_passes_in_both_modes():
    for standalone in (False, True):
        ok, result = run_skill("valid", standalone=standalone)
        assert ok, result["errors"]
        assert result["warnings"] == []
        assert (result["skill"], result["kind"]) == ("valid", "skill")


def test_yaml_colon_description_is_refused():
    ok, result = run_skill("yaml-colon")
    assert not ok
    assert len(result["errors"]) == 1, result["errors"]
    assert result["errors"][0].startswith("description:") and "silently drops" in result["errors"][0]
    assert 'description: "Read the owner' in result["errors"][0]  # the fix is spelled out


def test_openclaw_metadata_is_refused_naming_the_retired_runtime():
    ok, result = run_skill("openclaw-meta")
    assert not ok
    for field in ("metadata.openclaw", "metadata.butler.tier", "metadata.butler.modes", "metadata.butler.params",
                  "metadata.butler.web3", "metadata.butler.requires.routes"):
        assert any(e.startswith(field + ":") and "OpenClaw" in e for e in result["errors"]), (field, result["errors"])


def test_unknown_commands_are_refused():
    ok, result = run_skill("unknown-cmd")
    assert not ok
    errors = result["errors"]
    assert all(e.startswith("command-allowlist:") for e in errors), errors
    assert any("'curl' is forbidden" in e for e in errors)
    assert any("'jq'" in e and "after a `|`" in e for e in errors)  # a pipe cannot smuggle a command
    assert any("`bevo-read frobnicate`" in e for e in errors)
    assert any("`acp configure` is refused" in e for e in errors)


def test_retired_runtime_mentions_are_refused():
    ok, result = run_skill("retired-refs")
    assert not ok
    assert all(e.startswith("retired-runtime-lint:") for e in result["errors"]), result["errors"]
    joined = "\n".join(result["errors"])
    for term in ("'AGENTS.md'", "'bevo-hub'", "'OpenClaw'"):
        assert term in joined


def test_money_command_outside_a_fixed_step_is_refused():
    ok, result = run_skill("money-unfixed")
    assert not ok
    assert len(result["errors"]) == 1 and result["errors"][0].startswith("steps:"), result["errors"]
    assert "[FIXED]" in result["errors"][0] and "acp trade" in result["errors"][0]


def test_commands_must_be_declared_in_requires_bins():
    ok, result = run_skill("bins-undeclared")
    assert not ok
    assert sorted(result["errors"]) == [
        "metadata.butler.requires.bins: 'bevo-read' runs in a shell block but is not declared — add it to requires.bins",
        "metadata.butler.requires.bins: 'bevo-sms' runs in a shell block but is not declared — add it to requires.bins",
    ]


# --- the kind is read off the repo ------------------------------------------------------------


def test_the_kind_is_detected_and_a_repo_holding_both_is_refused(tmp_path):
    assert validate.detect_kind(FIXTURES / "valid") == "template"
    assert validate.detect_kind(SKILL_FIXTURES / "valid") == "skill"
    ok, result = validate.validate_path(FIXTURES / "valid", set(), False, True, standalone=True)
    assert ok and result["template"] == "valid"
    ok, result = validate.validate_path(SKILL_FIXTURES / "valid", set(), False, True, standalone=True)
    assert ok and result["kind"] == "skill"

    d = write_skill(tmp_path)
    (d / "recipe.json").write_text("{}")
    ok, result = validate.validate_path(d, set(), False, True, standalone=True)
    assert not ok
    assert result["errors"] == [
        "layout: holds both SKILL.md and recipe.json — a repository is one kind: a skill (SKILL.md) or a duty template (recipe.json)"
    ]


def test_a_listing_must_hold_its_own_kind():
    ok, result = validate.validate_path(FIXTURES / "valid", set(), False, True, expected_kind="skill")
    assert not ok and "belongs in templates.json" in result["errors"][0]
    ok, result = validate.validate_path(SKILL_FIXTURES / "valid", set(), False, True, expected_kind="template")
    assert not ok and "belongs in skills.json" in result["errors"][0]


def test_cli_standalone_validates_a_skill_repo_named_anything(tmp_path):
    repo = tmp_path / "butler-skill-whatever"
    shutil.copytree(SKILL_FIXTURES / "valid", repo)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate.py"), "--standalone", str(repo), "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] and out["results"][0]["skill"] == "valid" and out["results"][0]["kind"] == "skill"


# --- frontmatter: what Mastra (gray-matter + js-yaml) reads back -------------------------------


def test_a_description_quoted_as_a_json_string_may_hold_anything(tmp_path):
    d = write_skill(tmp_path, fm={"description": '"Read it: all of it # every line"'})
    ok, result = check_skill(d)
    assert ok, result["errors"]
    fm = validate.parse_skill_md((d / "SKILL.md").read_text(), validate.Issues())
    assert fm["description"] == "Read it: all of it # every line"


@pytest.mark.parametrize("description", [
    "Ends in a colon:", "Has a # comment", "- starts like a list", "[starts like a flow list]",
    "*an alias", "'single quoted'", "`backticked`", "@at", "%percent", "123", "true", "null",
    "2026-09-21", '"unterminated',
])
def test_a_description_yaml_would_misread_is_refused(tmp_path, description):
    ok, result = check_skill(write_skill(tmp_path, fm={"description": description}))
    assert not ok
    assert any(e.startswith("description:") for e in result["errors"]), result["errors"]


def test_description_length_and_shape(tmp_path):
    ok, result = check_skill(write_skill(tmp_path / "a", fm={"description": "x" * 201}))
    assert not ok and any("must be <= 200 chars" in e for e in result["errors"])
    ok, result = check_skill(write_skill(tmp_path / "b", fm={"description": '"two\\nlines"'}))
    assert not ok and any("control characters" in e for e in result["errors"])


def test_frontmatter_keys_are_exactly_the_four(tmp_path):
    ok, result = check_skill(write_skill(tmp_path / "a", extra_fm_lines=["license: MIT"]))
    assert not ok and any("unknown key 'license'" in e for e in result["errors"])
    ok, result = check_skill(write_skill(tmp_path / "b", extra_fm_lines=["version: 1.0.1"]))
    assert not ok and any("'version' given twice" in e for e in result["errors"])
    ok, result = check_skill(write_skill(tmp_path / "c", fm={"version": None}))
    assert not ok and "version: required field missing" in result["errors"]
    for bad in ("1.0", "v1.0.0", "1.0.0-beta"):
        ok, result = check_skill(write_skill(tmp_path / f"v{bad}", fm={"version": bad}))
        assert not ok and any(e.startswith("version:") and "semver" in e for e in result["errors"]), bad


def test_frontmatter_characters_yaml_reads_differently_are_refused(tmp_path):
    # Python's splitlines() would read this as two valid lines; js-yaml reads one bad one.
    ok, result = check_skill(write_skill(tmp_path / "a", fm={"description": "Fine\u2028name: foo"}))
    assert not ok and any("U+2028" in e and "js-yaml refuses" in e for e in result["errors"])
    ok, result = check_skill(write_skill(tmp_path / "b", fm={"description": "C1\x85control"}))
    assert not ok and any("U+0085" in e for e in result["errors"])


def test_frontmatter_needs_both_fences(tmp_path):
    d = write_skill(tmp_path)
    (d / "SKILL.md").write_text("name: foo\n")
    ok, result = check_skill(d)
    assert not ok and "frontmatter: SKILL.md must start with a '---' line" in result["errors"]
    (d / "SKILL.md").write_text("---\nname: foo\n")
    ok, result = check_skill(d)
    assert not ok and "frontmatter: no closing '---' line" in result["errors"]


def test_metadata_is_one_line_of_json_holding_only_the_butler_block(tmp_path):
    block = write_skill(tmp_path / "a", fm={"metadata": None}, extra_fm_lines=["metadata:", "  butler: {}"])
    ok, result = check_skill(block)
    assert not ok
    assert any(e.startswith("metadata:") and "same line" in e for e in result["errors"])
    assert any(e.startswith("frontmatter:") and "not a one-line" in e for e in result["errors"])

    dup = write_skill(tmp_path / "b", fm={"metadata": '{"butler":{},"butler":{}}'})
    ok, result = check_skill(dup)
    assert not ok and any("duplicated key 'butler'" in e for e in result["errors"])

    extra = write_skill(tmp_path / "c", fm={"metadata": (
        '{"other":1,"butler":{"moneyMoving":"yes","keywords":[""],"extra":1,'
        '"requires":{"bins":["acp","curl","acp"],"gates":["canSwap"]}}}')})
    ok, result = check_skill(extra)
    errors = result["errors"]
    assert "metadata.other: unknown key — metadata holds only the \"butler\" block" in errors
    assert any(e.startswith("metadata.butler.extra: unknown key") for e in errors)
    assert "metadata.butler.moneyMoving: must be true or false" in errors
    assert "metadata.butler.keywords: must be an array of non-empty strings" in errors
    assert any(e.startswith("metadata.butler.requires.bins:") and "'curl'" in e for e in errors)
    assert "metadata.butler.requires.bins: lists a command twice" in errors
    assert any(e.startswith("metadata.butler.requires.gates:") and "OpenClaw" in e for e in errors)


# --- names ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Foo", "foo--bar", "foo-", "foo_bar", "a" * 65, "123", "1e5", "2026-09-21"])
def test_a_skill_name_mastra_would_drop_is_refused(tmp_path, name):
    ok, result = check_skill(write_skill(tmp_path / "x", name=name))
    assert not ok
    assert any(e.startswith("name:") for e in result["errors"]), result["errors"]


def test_registry_mode_requires_the_name_to_equal_the_directory(tmp_path):
    d = write_skill(tmp_path, name="foo")
    ok, _ = check_skill(d, standalone=False)
    assert ok
    moved = tmp_path / "bar"
    d.rename(moved)
    ok, result = check_skill(moved, standalone=False)
    assert not ok and any(e.startswith("name:") and "must equal directory name 'bar'" in e for e in result["errors"])
    ok, _ = check_skill(moved, standalone=True)
    assert ok


def test_skill_name_prefixes_and_reserved_names(tmp_path):
    reserved = validate.load_reserved()
    for name in ("duty-code", "acp-cli"):  # the two skills compiled into the image
        assert name in reserved
        ok, result = check_skill(write_skill(tmp_path, name=name), reserved=reserved)
        assert not ok and any("is reserved" in e for e in result["errors"])
    d = write_skill(tmp_path, name="butler-thing")
    ok, result = check_skill(d)
    assert not ok and any("maintainer-only 'butler-' prefix" in e for e in result["errors"])
    assert check_skill(d, maintainer=True)[0]
    d = write_skill(tmp_path, name="bevo-thing")
    for maintainer in (False, True):
        ok, result = check_skill(d, maintainer=maintainer)
        assert not ok and any("bundled-command" in e for e in result["errors"])


# --- body: sections and steps ----------------------------------------------------------------


def test_sections_are_the_seven_in_order(tmp_path):
    body = (
        "## When to use\n\nx\n\n## Procedure\n\n1. [FIXED] Read.\n\n## Before you start\n\nx\n\n"
        "## One-off procedure\n\nx\n\n## Notes\n\nx\n\n## Idempotency and retries\n\ndo not re-run\n\n"
        "## Failure handling\n\nx\n\n## Limits\n\nx\n"
    )
    ok, result = check_skill(write_skill(tmp_path, body=body))
    errors = result["errors"]
    assert not ok
    assert any("`## One-off procedure` is not a skill section — renamed" in e for e in errors)
    assert any("`## Notes` is not a skill section — put extra material under a `###`" in e for e in errors)
    assert "sections: missing required section `## Say to the owner`" in errors
    assert any(e.startswith("sections: sections out of order") for e in errors)


def test_procedure_steps_carry_a_marker(tmp_path):
    ok, result = check_skill(write_skill(tmp_path / "a", body=procedure_body("1. Read it.\n2. [ADAPT] Say it.\n")))
    assert not ok and any("numbered step missing [FIXED]/[ADAPT] marker" in e for e in result["errors"])
    ok, result = check_skill(write_skill(tmp_path / "b", body=procedure_body("Just prose.\n")))
    assert not ok and "steps: `## Procedure` has no numbered steps (each one [FIXED] or [ADAPT])" in result["errors"]
    # numbered lists elsewhere are prose, not steps
    body = procedure_body("1. [FIXED] Read.\n").replace("## Limits\n\nx\n", "## Limits\n\n1. One.\n2. Two.\n")
    assert check_skill(write_skill(tmp_path / "c", body=body))[0]


def test_a_money_moving_skill_must_say_do_not_re_run(tmp_path):
    body = procedure_body("1. [FIXED] Read.\n").replace("do not re-run a money command", "retry freely")
    ok, result = check_skill(write_skill(tmp_path, body=body))
    assert not ok and any("must say 'do not re-run'" in e for e in result["errors"])


@pytest.mark.parametrize("command", [
    "acp trade --token-in usdc --amount-in 5 --token-out <T> --json",
    "acp --json trade --side long --token BTC --amount-usdc 20 --leverage 2",
    "acp wallet send-transaction --to <ADDRESS> --data <HEX> --json",
    "acp card issue --amount 500 --merchant <M> --purpose <P> --json",
    "bevo-send --to @someone --amount 1 --token usdc",
    "app-checkout checkpoint --app GrabFood --amount 12.40 --currency MYR --wait 0",
])
def test_every_money_command_needs_a_fixed_step(tmp_path, command):
    bins = '["acp","bevo-send","app-checkout"]'
    fm = {"metadata": '{"butler":{"moneyMoving":true,"keywords":["x"],"requires":{"bins":' + bins + '}}}'}
    fixed = procedure_body(f"1. [FIXED] Do it:\n\n   ```sh\n   {command}\n   ```\n")
    assert check_skill(write_skill(tmp_path / "fixed", fm=fm, body=fixed))[0]
    adapt = fixed.replace("[FIXED]", "[ADAPT]")
    ok, result = check_skill(write_skill(tmp_path / "adapt", fm=fm, body=adapt))
    assert not ok and any(e.startswith("steps:") and "[FIXED]" in e for e in result["errors"])
    # a heading ends the step: a block under a later subsection is not inside it
    later = fixed.replace("1. [FIXED] Do it:\n", "1. [FIXED] Do it.\n\n### Then\n")
    ok, result = check_skill(write_skill(tmp_path / "later", fm=fm, body=later))
    assert not ok and any(e.startswith("steps:") for e in result["errors"])
    # and a money command is never outside `## Procedure`
    outside = fixed.replace("## Limits\n\nx\n", f"## Limits\n\n```sh\n{command}\n```\n")
    ok, result = check_skill(write_skill(tmp_path / "outside", fm=fm, body=outside))
    assert not ok and any(e.startswith("steps:") for e in result["errors"])


def test_money_commands_need_money_moving_true(tmp_path):
    fm = {"metadata": '{"butler":{"moneyMoving":false,"keywords":["x"],"requires":{"bins":["acp","bevo-read","bevo-notify"]}}}'}
    ok, result = check_skill(write_skill(tmp_path, fm=fm))
    assert not ok and any(e.startswith("metadata.butler.moneyMoving: is false") for e in result["errors"])


def test_reads_are_not_money_and_need_no_fixed_step(tmp_path):
    fm = {"metadata": '{"butler":{"moneyMoving":false,"keywords":["x"],"requires":{"bins":["acp","bevo-read"]}}}'}
    steps = "1. [ADAPT] Look:\n\n   ```sh\n   acp wallet balance --ticker ETH --json\n   bevo-read assets\n   ```\n"
    ok, result = check_skill(write_skill(tmp_path, fm=fm, body=procedure_body(steps, moneyish=False)))
    assert ok, result["errors"]


def test_body_size_and_unclosed_fence(tmp_path):
    ok, result = check_skill(write_skill(tmp_path / "a", body=VALID_SKILL_BODY + "\n" + "x" * 12000))
    assert not ok and any(e.startswith("body: body is") for e in result["errors"])
    ok, result = check_skill(write_skill(tmp_path / "b", body=VALID_SKILL_BODY + "\n```sh\nbevo-read me\n"))
    assert not ok and any("code fence is never closed" in e for e in result["errors"])


# --- body: the commands a skill may run -------------------------------------------------------


def _commands(tmp_path, lines: str, bins: str = '["acp","app-checkout","bevo-automation","bevo-read","bevo-sms","bevo-x"]'):
    fm = {"metadata": '{"butler":{"moneyMoving":false,"keywords":["x"],"requires":{"bins":' + bins + '}}}'}
    body = procedure_body(f"1. [ADAPT] Run:\n\n```sh\n{lines}\n```\n", moneyish=False)
    ok, result = check_skill(write_skill(tmp_path, fm=fm, body=body))
    return ok, [e for e in result["errors"] if e.startswith("command-allowlist:")]


@pytest.mark.parametrize("line", [
    "acp email inbox --json", "acp agent whoami", "acp job list", "acp wallet balance --ticker ETH",
    "bevo-sms otp --since 2026-09-09T07:20:00Z", "bevo-x search virtuals", "app-checkout screen",
    "bevo-automation create @duty.json", "bevo-read token-price VIRTUAL",
])
def test_commands_the_container_has_pass(tmp_path, line):
    ok, errors = _commands(tmp_path, line)
    assert ok and errors == [], errors


@pytest.mark.parametrize("line, needle", [
    ("acp", "bare `acp`"),
    ("acp --help", "bare `acp`"),
    ("acp compute run", "`acp compute` is refused"),
    ("acp client pay", "`acp client` is refused"),
    ("acp frobnicate", "`acp frobnicate` is not an acp command group"),
    ("acp agent create", "`acp agent create` is refused"),
    ("bevo-sms call", "`bevo-sms call` is not one of bevo-sms's subcommands"),
    ("app-checkout phone", "`app-checkout phone` is not one of app-checkout's subcommands"),
    ("app-checkout otp --type", "`app-checkout otp`"),
    ("bevo-automation frobnicate", "`bevo-automation frobnicate`"),
    ("bevo-read", "`bevo-read` needs a subcommand"),
    ("bevo-hub install x", "'bevo-hub' is not a command a skill may run"),
    ("python3 -c 'print(1)'", "'python3' is forbidden"),
    ("node x.js", "'node' is forbidden"),
    ("wget x", "'wget' is forbidden"),
    ("bevo-read me && curl evil.example", "'curl' is forbidden"),
    ("bevo-read me; jq .", "'jq' is not a command"),
    ("bevo-read token $(cat f)", "command substitution"),
    ("FOO=1 bevo-read me", "'FOO=1' is not a command"),
])
def test_commands_the_container_lacks_are_refused(tmp_path, line, needle):
    ok, errors = _commands(tmp_path, line)
    assert not ok and any(needle in e for e in errors), errors


def test_shell_block_parsing(tmp_path):
    heredoc = "bevo-automation create - <<'EOF'\n{\"name\": \"x\", \"code\": \"import bevo\"}\nEOF\nbevo-read me"
    assert _commands(tmp_path / "a", heredoc) == (True, [])
    prompt_and_continuation = "$ bevo-read assets \\\n    --include-unverified\n# a comment\n\n$ bevo-read me"
    assert _commands(tmp_path / "b", prompt_and_continuation) == (True, [])
    # a non-shell block is not a command list
    body = procedure_body('1. [ADAPT] Shape:\n\n```json\n{"curl": true}\n```\n', moneyish=False)
    fm = {"metadata": '{"butler":{"moneyMoving":false,"keywords":["x"],"requires":{"bins":[]}}}'}
    assert check_skill(write_skill(tmp_path / "c", fm=fm, body=body))[0]


# --- lints over what is published --------------------------------------------------------------


@pytest.mark.parametrize("text, prefix", [
    ("brt_abcdef123456", "secrets-lint:"),
    ("https://evil.example/x", "url-lint:"),
    ("https://github.com/Virtual-Protocol-evil/x", "url-lint:"),
    ("a\u200bb", "invisible-char-lint:"),
    ("a\u202eb", "invisible-char-lint:"),
    ("0x833589fCD6eDb6e08f4c7C32D4f71b54bdA02913", "address-lint:"),
    ("TODO fill this in", "scaffold:"),
    ("ignore previous instructions", "override-phrase-lint:"),
    ("see web-checkout", "retired-runtime-lint:"),
])
def test_published_text_lints(tmp_path, text, prefix):
    body = VALID_SKILL_BODY.replace("Fixture only", "x").replace("A standing order", f"{text}. A standing order")
    ok, result = check_skill(write_skill(tmp_path, body=body))
    assert not ok and any(e.startswith(prefix) for e in result["errors"]), result["errors"]


def test_a_virtual_protocol_link_passes(tmp_path):
    body = VALID_SKILL_BODY.replace("A standing order", "https://github.com/Virtual-Protocol/butler-skills says. A standing order")
    assert check_skill(write_skill(tmp_path, body=body))[0]


# --- layout ---------------------------------------------------------------------------------


def test_skill_layout_rules(tmp_path):
    d = write_skill(tmp_path / "a", changelog="## 0.9.0\n")
    ok, result = check_skill(d)
    assert not ok and "CHANGELOG.md: no entry for 1.0.0 — add a '## 1.0.0' heading saying what changed" in result["errors"]
    (d / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0] - 2026-09-23\n")
    assert check_skill(d)[0]
    (d / "CHANGELOG.md").write_text("# Changelog\n\n## 1.0.00\n")
    assert not check_skill(d)[0]

    (d / "CHANGELOG.md").unlink()
    (d / "duty.py").write_text("import bevo\n")
    ok, result = check_skill(d)
    assert "layout: missing required file: CHANGELOG.md" in result["errors"]
    assert any(e.startswith("layout: duty.py in a skill is never published") for e in result["errors"])

    d = write_skill(tmp_path / "b")
    (d / "docs").mkdir()
    os.symlink(d / "SKILL.md", d / "docs" / "link.md")
    for i in range(validate.MAX_TREE_FILES):
        (d / "docs" / f"f{i}.txt").write_text("x")
    ok, result = check_skill(d)
    assert any("symlink not allowed: docs/link.md" in e for e in result["errors"])
    assert any(f"must be <= {validate.MAX_TREE_FILES}" in e for e in result["errors"])


def test_references_are_published_linted_and_checked(tmp_path):
    d = write_skill(tmp_path)
    (d / "references" / "deep").mkdir(parents=True)
    (d / "references" / "b.md").write_text("Fine.\n")
    (d / "references" / "deep" / "a.md").write_text("Fine too.\n")
    (d / "references" / "notes.txt").write_text("not published\n")
    (d / "scripts").mkdir()
    (d / "scripts" / "x.sh").write_text("echo\n")
    assert validate.skill_published_files(d) == ["SKILL.md", "references/b.md", "references/deep/a.md"]
    ok, result = check_skill(d)
    assert ok, result["errors"]
    assert any("references/notes.txt is not published" in w for w in result["warnings"])
    assert any("scripts/ is not published" in w for w in result["warnings"])

    (d / "references" / "b.md").write_text("```sh\ncurl x\nacp trade --token-in usdc\n```\nSee AGENTS.md.\n")
    (d / "references" / ".hidden.md").write_text("x\n")
    ok, result = check_skill(d)
    errors = result["errors"]
    assert any(e.startswith("command-allowlist: references/b.md line 2") for e in errors)
    assert any(e.startswith("steps: references/b.md line 3: a money command belongs in a [FIXED] step") for e in errors)
    assert any(e.startswith("retired-runtime-lint: references/b.md line 5") for e in errors)
    assert any("references/.hidden.md: a published reference path" in e for e in errors)


# --- registry mode: skills.json ------------------------------------------------------------------


def test_load_skills_registry_reads_skills_json(tmp_path):
    rows = [{"name": "foo", "repo": "https://github.com/someone/butler-skill-foo", "ref": "main"}]
    (tmp_path / "skills.json").write_text(json.dumps({"skills": rows}))
    assert validate.load_skills_registry(tmp_path / "skills.json") == rows
    (tmp_path / "empty.json").write_text(json.dumps({"skills": []}))
    assert validate.load_skills_registry(tmp_path / "empty.json") == []
    (tmp_path / "malformed.json").write_text(json.dumps({"templates": []}))
    with pytest.raises(SystemExit):
        validate.load_skills_registry(tmp_path / "malformed.json")
    with pytest.raises(SystemExit):
        validate.load_skills_registry(tmp_path / "missing.json")


def _git_repo(root: Path) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=str(root), check=True, capture_output=True, env=env)
    return root.as_uri()


def test_all_validates_both_listings(tmp_path, monkeypatch, capsys):
    """`--all` clones templates.json AND skills.json, and holds each entry to its own kind."""
    _write_minimal_template(tmp_path / "src-tmpl", "tmpl")
    skill_src = tmp_path / "src-skill"
    shutil.copytree(SKILL_FIXTURES / "valid", skill_src)
    (tmp_path / "templates.json").write_text(json.dumps({"templates": [
        {"name": "tmpl", "repo": _git_repo(tmp_path / "src-tmpl"), "ref": "main"}]}))
    (tmp_path / "skills.json").write_text(json.dumps({"skills": [
        {"name": "valid", "repo": _git_repo(skill_src), "ref": "main"}]}))
    monkeypatch.setattr(validate, "REGISTRY_PATH", tmp_path / "templates.json")
    monkeypatch.setattr(validate, "SKILLS_REGISTRY_PATH", tmp_path / "skills.json")
    monkeypatch.setattr(sys, "argv", ["validate.py", "--all", "--json"])
    assert validate.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and {r.get("template") or r.get("skill") for r in out["results"]} == {"tmpl", "valid"}

    # the same skill repo listed as a template is refused for being the wrong kind
    (tmp_path / "templates.json").write_text(json.dumps({"templates": [
        {"name": "valid", "repo": skill_src.as_uri(), "ref": "main"}]}))
    (tmp_path / "skills.json").write_text(json.dumps({"skills": []}))
    assert validate.main() == 1
    assert "belongs in skills.json" in capsys.readouterr().out


def test_all_over_two_empty_listings_passes(tmp_path, monkeypatch, capsys):
    (tmp_path / "templates.json").write_text(json.dumps({"templates": []}))
    (tmp_path / "skills.json").write_text(json.dumps({"skills": []}))
    monkeypatch.setattr(validate, "REGISTRY_PATH", tmp_path / "templates.json")
    monkeypatch.setattr(validate, "SKILLS_REGISTRY_PATH", tmp_path / "skills.json")
    monkeypatch.setattr(sys, "argv", ["validate.py", "--all"])
    assert validate.main() == 0
    assert "nothing to validate" in capsys.readouterr().out
