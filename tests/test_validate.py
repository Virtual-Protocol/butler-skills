"""test_validate.py — pytest coverage for scripts/validate.py against the
fixture skills under tests/fixtures/skills/{valid, missing-key,
bad-frontmatter, oversize-description, banned-command, undeclared-param,
unmarked-step}, plus the git-backed rules: --standalone mode (name from the
frontmatter), the tree rules (symlinks, nested submodules/repos, the 50-file
and 1 MB caps) and registry mode (the checkout is named after its skills.json
entry; `--all` clones every entry).
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


def test_the_pre_rename_bevo_block_is_rejected(tmp_path):
    """The namespace key is `metadata.butler`. `metadata.bevo` is the
    pre-rename spelling and is refused outright — this is a hard cut, so a
    skill carrying the old key must fail here rather than validate and then
    read as an empty block in the container."""
    skill_dir = tmp_path / "butler-old-key"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: butler-old-key\ndescription: x\nversion: 1.0.0\n'
        'metadata: {"bevo":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n"
        "## One-off procedure\n1. [FIXED] x\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill_dir / "CHANGELOG.md").write_text("# Changelog\n")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=True, json_mode=True)
    assert not ok
    assert any(e.startswith("metadata.butler: required block missing") for e in result["errors"])


def test_reserved_name_rejected(tmp_path):
    reserved = {"web-checkout"}
    skill_dir = tmp_path / "web-checkout"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: web-checkout\ndescription: x\nversion: 1.0.0\n'
        'metadata: {"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n"
        "## One-off procedure\n1. [FIXED] x\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill_dir / "CHANGELOG.md").write_text("# Changelog\n")
    ok, result = validate.validate_skill(skill_dir, reserved, maintainer=False, json_mode=True)
    assert not ok
    assert any("reserved" in e for e in result["errors"])


def test_butler_prefix_requires_maintainer(tmp_path):
    reserved: set[str] = set()
    skill_dir = tmp_path / "butler-new-thing"
    _write_minimal_skill(skill_dir, "butler-new-thing")
    ok, result = validate.validate_skill(skill_dir, reserved, maintainer=False, json_mode=True)
    assert not ok
    assert any("maintainer-only 'butler-' prefix" in e for e in result["errors"])
    ok2, _ = validate.validate_skill(skill_dir, reserved, maintainer=True, json_mode=True)
    assert ok2


def test_bevo_prefix_is_refused_even_for_maintainers(tmp_path):
    # bevo-* is the container's bundled-skill namespace (bevo-hub, bevo-onchain, ...).
    skill_dir = tmp_path / "bevo-new-thing"
    _write_minimal_skill(skill_dir, "bevo-new-thing")
    for maintainer in (False, True):
        ok, result = validate.validate_skill(skill_dir, set(), maintainer=maintainer, json_mode=True)
        assert not ok
        assert any(e.startswith("name:") and "bundled-skill namespace" in e for e in result["errors"]), result["errors"]
        assert not any("maintainer-only" in e for e in result["errors"])


def test_embedded_reserved_list_matches_schema_json():
    # validate.py is published as a single standalone file, so it carries a mirror of
    # schema/reserved-names.json; this is the only thing keeping the two in sync.
    import json

    on_disk = set(json.loads((REPO_ROOT / "schema" / "reserved-names.json").read_text())["reserved"])
    assert set(validate.RESERVED_NAMES_BUILTIN) == on_disk
    assert validate.load_reserved() == on_disk


def test_load_reserved_falls_back_to_embedded_list_without_schema_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "RESERVED_PATH", tmp_path / "does-not-exist.json")
    assert validate.load_reserved() == set(validate.RESERVED_NAMES_BUILTIN)


def _write_web3_skill(skill_dir: Path, contracts_json: str, with_contracts_section: bool) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    section = "## Contracts\n|a|b|\n|-|-|\n" if with_contracts_section else ""
    (skill_dir / "SKILL.md").write_text(
        "---\nname: foo\ndescription: x\nversion: 1.0.0\n"
        'metadata: {"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":true,'
        '"web3":{"chains":[8453],"contracts":' + contracts_json + "}}}\n---\n\n"
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n" + section +
        "## One-off procedure\n1. [FIXED] x\n\n   ```bash\n   acp wallet send-transaction --chain-id 8453 --to 0x --data 0x --idempotency-key k\n   ```\n\n"
        "## Idempotency and retries\ndo not re-run\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill_dir / "CHANGELOG.md").write_text("# Changelog\n")


def test_web3_skill_with_empty_contracts_needs_no_contracts_section(tmp_path):
    # A skill that takes the contract address as an owner-supplied param: contracts: [] is fine.
    skill_dir = tmp_path / "foo"
    _write_web3_skill(skill_dir, "[]", with_contracts_section=False)
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]


def test_web3_skill_with_listed_contracts_still_needs_contracts_section(tmp_path):
    skill_dir = tmp_path / "foo"
    contracts = '[{"name":"USDC","chainId":8453,"address":"0x833589fCD6eDb6e08f4c7C32D4f71b54bdA02913","functions":[]}]'
    _write_web3_skill(skill_dir, contracts, with_contracts_section=False)
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("web3:") and "## Contracts" in e for e in result["errors"])
    _write_web3_skill(skill_dir, contracts, with_contracts_section=True)
    ok2, result2 = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok2, result2["errors"]


def test_send_transaction_without_any_web3_block_is_still_refused(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_web3_skill(skill_dir, "[]", with_contracts_section=False)
    text = (skill_dir / "SKILL.md").read_text().replace(',"web3":{"chains":[8453],"contracts":[]}', "")
    (skill_dir / "SKILL.md").write_text(text)
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert not ok
    assert any(e.startswith("web3:") and "declares no metadata.butler.web3 block" in e for e in result["errors"])


def test_downloaded_tooling_in_the_tree_is_a_warning_not_an_error(tmp_path):
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "foo")
    (skill_dir / "validate.py").write_text("# downloaded\n")
    (skill_dir / "replay.py").write_text("# downloaded\n")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok, result["errors"]
    assert any("validate.py looks like downloaded hub tooling" in w for w in result["warnings"])
    assert any("replay.py looks like downloaded hub tooling" in w for w in result["warnings"])


# --- git-backed registry: --standalone mode, tree rules, pin rules -----------------


def _write_minimal_skill(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: x\nversion: 1.0.0\n'
        'metadata: {"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
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


def test_registry_mode_requires_the_frontmatter_name_to_match_the_registry_name(tmp_path):
    """Registry mode's remaining directory rule: `--all` clones each entry into a
    directory named after its skills.json entry, so a skill whose frontmatter
    disagrees would be installed under a name the registry never listed."""
    skill_dir = tmp_path / "foo"
    _write_minimal_skill(skill_dir, "bar")
    ok, result = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=False)
    assert not ok
    assert any(e.startswith("name:") and "must equal directory name" in e for e in result["errors"])
    # --standalone drops that rule: the author's checkout is named whatever they cloned it as.
    ok2, _ = validate.validate_skill(skill_dir, set(), maintainer=False, json_mode=True, standalone=True)
    assert ok2


def test_load_registry_reads_skills_json(tmp_path):
    registry = tmp_path / "skills.json"
    rows = [{"name": "foo", "repo": "https://github.com/someone/butler-skill-foo", "ref": "main"}]
    registry.write_text(json.dumps({"skills": rows}))
    assert validate.load_registry(registry) == rows

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"skills": []}))
    with pytest.raises(SystemExit):
        validate.load_registry(empty)
    with pytest.raises(SystemExit):
        validate.load_registry(tmp_path / "missing.json")


def _make_skill_repo(root: Path, name: str) -> str:
    """A real local git repo holding one skill, so clone_registry_skills has
    something to clone without touching the network."""
    _write_minimal_skill(root, name)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=str(root), check=True, capture_output=True, env=env)
    return root.as_uri()


def test_all_clones_every_registry_entry_into_a_directory_named_for_it(tmp_path, monkeypatch):
    """No skill is checked out here, so `--all` fetches what it validates. The
    clone directory takes the registry name, which is what makes the
    frontmatter-name check above mean anything."""
    registry = tmp_path / "skills.json"
    registry.write_text(json.dumps({"skills": [
        {"name": "foo", "repo": _make_skill_repo(tmp_path / "src-foo", "foo"), "ref": "main"},
        {"name": "bar", "repo": _make_skill_repo(tmp_path / "src-bar", "bar"), "ref": "main"},
    ]}))
    monkeypatch.setattr(validate, "REGISTRY_PATH", registry)

    work = tmp_path / "work"
    work.mkdir()
    dirs = validate.clone_registry_skills(work)
    assert [d.name for d in dirs] == ["bar", "foo"]  # sorted
    for d in dirs:
        assert (d / "SKILL.md").exists()
        ok, result = validate.validate_skill(d, set(), maintainer=False, json_mode=True, standalone=False)
        assert ok, (d, result["errors"])


def test_clone_registry_skills_fails_loudly_on_a_ref_that_does_not_resolve(tmp_path, monkeypatch):
    registry = tmp_path / "skills.json"
    registry.write_text(json.dumps({"skills": [
        {"name": "foo", "repo": _make_skill_repo(tmp_path / "src-foo", "foo"), "ref": "no-such-ref"},
    ]}))
    monkeypatch.setattr(validate, "REGISTRY_PATH", registry)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit) as e:
        validate.clone_registry_skills(work)
    assert "no-such-ref" in str(e.value)


def test_the_real_copytrade_skill_passes_in_both_modes(copytrade_checkout):
    """The registry's own butler-copytrade, cloned at the ref skills.json
    follows (tests/conftest.py), through both modes of the validator."""
    reserved = validate.load_reserved()
    ok, result = validate.validate_skill(copytrade_checkout, reserved, maintainer=True, json_mode=True)
    assert ok, result["errors"]
    ok2, result2 = validate.validate_skill(copytrade_checkout, reserved, maintainer=True, json_mode=True, standalone=True)
    assert ok2, result2["errors"]
    assert result2["skill"] == "butler-copytrade"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
