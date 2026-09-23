"""test_standalone_tools.py — the developer path that never clones this registry.

publish.yml lays scripts/validate.py, tests/replay.py, tests/stub_bevo.py and
tests/fixtures/* out under dist/tools/ (scripts/publish_tools.py). A template
author downloads only validate.py and replay.py; replay.py fetches
stub_bevo.py and any fixture it needs from the same site. These tests
exercise exactly that layout from an empty directory, with a file:// mirror
standing in for the Pages site so nothing touches the network — using the
local `valid` fixture template (tests/fixtures/templates/valid) as the
template under test, since no template repo is checked out in this
repository.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID = REPO_ROOT / "tests" / "fixtures" / "templates" / "valid"


def _load_publish_tools():
    spec = importlib.util.spec_from_file_location("butler_skills_publish_tools", REPO_ROOT / "scripts" / "publish_tools.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


publish_tools = _load_publish_tools()


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return mods


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), env=full_env)


def _copy_template(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return dst


def _actions(stdout: str) -> list[dict]:
    return json.loads(stdout.split("# ACTIONS_JSON_START\n")[1].split("# ACTIONS_JSON_END")[0])


# --- the files themselves ----------------------------------------------------------------


def test_published_tools_are_single_files_using_only_the_stdlib():
    stdlib = sys.stdlib_module_names
    for rel in ("scripts/validate.py", "tests/replay.py", "tests/stub_bevo.py", "scripts/publish_tools.py"):
        mods = _imports_of(REPO_ROOT / rel)
        assert mods <= stdlib, f"{rel} imports non-stdlib / sibling modules: {sorted(mods - stdlib)}"


def test_publish_tools_layout(tmp_path):
    written = publish_tools.publish(tmp_path / "dist")
    rel = sorted(p.relative_to(tmp_path / "dist").as_posix() for p in written)
    names = set(rel)
    assert {"tools/validate.py", "tools/replay.py", "tools/stub_bevo.py"} <= names
    assert not any(n.startswith("tools/check_selectors") for n in names)  # selector recomputation is gone
    fixture_files = {f.name for f in (REPO_ROOT / "tests" / "fixtures").iterdir() if f.is_file()}
    assert fixture_files, "no fixture files?"
    assert {f"tools/fixtures/{n}" for n in fixture_files} <= names
    assert not any(n.startswith("tools/fixtures/templates") for n in names)  # validator fixtures are not replay fixtures
    assert not any(n.startswith("tools/fixtures/skills") for n in names)
    assert (tmp_path / "dist" / "tools" / "validate.py").read_bytes() == (REPO_ROOT / "scripts" / "validate.py").read_bytes()
    assert (tmp_path / "dist" / "tools" / "replay.py").read_bytes() == (REPO_ROOT / "tests" / "replay.py").read_bytes()


def test_publish_tools_cli_writes_under_dist(tmp_path):
    proc = _run([sys.executable, str(REPO_ROOT / "scripts" / "publish_tools.py"), "--dist", str(tmp_path / "d")], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "d" / "tools" / "fixtures" / "trade-activity-page.jsonl").exists()
    assert "tool file(s) published under" in proc.stdout


# --- the developer path, from an empty directory ------------------------------------------


def test_validate_and_replay_from_the_published_layout(tmp_path):
    """Exactly what publish.yml serves, used from an unrelated cwd on a copy
    of the local `valid` fixture: validate.py alone (no schema/, no
    scripts/) and replay.py with stub + fixtures beside it (no download)."""
    tools = tmp_path / "site" / "tools"
    publish_tools.publish(tmp_path / "site")
    template = _copy_template(VALID, tmp_path / "my-template-checkout")

    v = _run([sys.executable, str(tools / "validate.py"), "--standalone", str(template)], cwd=tmp_path)
    assert v.returncode == 0, v.stdout + v.stderr
    assert "OK" in v.stdout

    r = _run(
        [sys.executable, str(tools / "replay.py"), "--standalone", str(template), "--fixture", "trade-activity-page",
         "--no-download"],
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "# standalone replay of valid from" in r.stdout
    assert len([a for a in _actions(r.stdout) if a["call"] == "acp"]) >= 1


def test_standalone_validate_uses_the_embedded_reserved_list(tmp_path):
    tools = tmp_path / "site" / "tools"
    publish_tools.publish(tmp_path / "site")
    template = tmp_path / "template"
    template.mkdir()
    (template / "recipe.json").write_text(json.dumps({
        "id": "clawhub", "version": 1, "description": "x", "params": {"type": "object", "properties": {}},
    }))
    (template / "duty.py").write_text("import bevo\nbevo.log('x')\n")
    (template / "README.md").write_text("# x\n")
    v = _run([sys.executable, str(tools / "validate.py"), "--standalone", str(template)], cwd=tmp_path)
    assert v.returncode == 1
    assert "'clawhub' is reserved" in v.stdout


def test_standalone_validate_detects_and_checks_a_skill(tmp_path):
    """The same published validate.py, pointed at a skill repo: the kind comes from
    the repo (SKILL.md), the embedded reserved list covers the image's own skills."""
    tools = tmp_path / "site" / "tools"
    publish_tools.publish(tmp_path / "site")
    skill = tmp_path / "butler-skill-valid"
    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "skills" / "valid", skill)
    v = _run([sys.executable, str(tools / "validate.py"), "--standalone", str(skill)], cwd=tmp_path)
    assert v.returncode == 0, v.stdout + v.stderr
    assert "OK" in v.stdout

    text = (skill / "SKILL.md").read_text().replace("name: valid", "name: duty-code", 1)
    (skill / "SKILL.md").write_text(text)
    v = _run([sys.executable, str(tools / "validate.py"), "--standalone", str(skill)], cwd=tmp_path)
    assert v.returncode == 1
    assert "'duty-code' is reserved" in v.stdout


def test_replay_downloads_stub_and_fixture_when_missing(tmp_path):
    """The author's real setup: only replay.py in the directory. stub_bevo.py
    and the fixture come from BUTLER_SKILLS_TOOLS_URL (a file:// mirror of
    the Pages layout)."""
    publish_tools.publish(tmp_path / "site")
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    template = _copy_template(VALID, tmp_path / "checkout")

    env = {"BUTLER_SKILLS_TOOLS_URL": (tmp_path / "site" / "tools").as_uri()}
    r = _run(
        [sys.executable, str(dev / "replay.py"), "--standalone", str(template), "--fixture", "trade-activity-page"],
        cwd=template, env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dev / "stub_bevo.py").exists()
    assert (dev / "fixtures" / "trade-activity-page.jsonl").exists()
    assert "# downloaded stub_bevo.py from" in r.stdout
    assert "# downloaded trade-activity-page.jsonl from" in r.stdout

    # second run: everything is beside replay.py now, nothing is fetched
    r2 = _run(
        [sys.executable, str(dev / "replay.py"), "--standalone", str(template), "--fixture", "trade-activity-page", "--no-download"],
        cwd=template, env=env,
    )
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "# downloaded" not in r2.stdout


def test_stub_downloads_a_read_fixture_on_first_use(tmp_path):
    """A duty that calls bevo.read("/me") with no local me.json gets it from the mirror."""
    publish_tools.publish(tmp_path / "site")
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    template = tmp_path / "template"
    template.mkdir()
    (template / "duty.py").write_text("import bevo\n\nme = bevo.read('/me')\nbevo.log(me['username'])\n")
    env = {"BUTLER_SKILLS_TOOLS_URL": (tmp_path / "site" / "tools").as_uri()}
    r = _run([sys.executable, str(dev / "replay.py"), "--standalone", str(template), "--fixture", "trade-activity-page"], cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[stub_bevo] downloaded fixture me.json" in r.stdout
    assert "[stub_bevo.log] owner" in r.stdout
    assert (dev / "fixtures" / "me.json").exists()


def test_replay_without_download_fails_loudly_on_a_missing_fixture(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    shutil.copy2(REPO_ROOT / "tests" / "stub_bevo.py", dev / "stub_bevo.py")
    template = _copy_template(VALID, tmp_path / "checkout")
    r = _run([sys.executable, str(dev / "replay.py"), "--standalone", str(template), "--fixture", "no-such-page", "--no-download"], cwd=tmp_path)
    assert r.returncode != 0
    assert "fixture no-such-page.jsonl not found" in (r.stdout + r.stderr)


def test_replay_is_skipped_cleanly_for_a_one_off_only_template(tmp_path):
    """A one-off-only template ships no duty.py: replay prints 'nothing to
    replay', exits 0, and never needs a stub or a fixture."""
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    template = _copy_template(VALID, tmp_path / "checkout")
    (template / "duty.py").unlink()
    r = _run([sys.executable, str(dev / "replay.py"), "--standalone", str(template), "--fixture", "trade-activity-page", "--no-download"], cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nothing to replay" in r.stdout
    assert not (dev / "stub_bevo.py").exists()


# --- the composite action ----------------------------------------------------------------


def test_composite_action_exists_with_the_documented_inputs():
    text = (REPO_ROOT / ".github" / "actions" / "validate" / "action.yml").read_text()
    assert "using: composite" in text
    for inp, default in (("path", '"."'), ("standalone", '"true"'), ("maintainer", '"false"'), ("fixture", "trade-activity-page")):
        assert f"  {inp}:" in text, inp
        assert default in text, (inp, default)
    assert "repository: Virtual-Protocol/butler-skills" in text
    assert "ref: main" in text
    assert "$RUNNER_TEMP/butler-skills" in text
    assert "scripts/validate.py" in text and "tests/replay.py" in text
    assert "check_selectors" not in text
    assert "viem" not in text
    assert "no duty.py" in text  # replay is skipped, not failed, for a template with none
    assert "trade-activity-mixed" in text
    assert "for FIXTURE in $FIXTURES; do" in text


def test_publish_workflow_publishes_the_tools():
    text = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text()
    assert "python3 scripts/publish_tools.py" in text
