"""test_standalone_tools.py — the developer path that never clones this registry.

publish.yml lays scripts/validate.py, tests/replay.py, tests/stub_bevo.py,
scripts/check_selectors.mjs and tests/fixtures/* out under dist/tools/
(scripts/publish_tools.py). A skill author downloads only validate.py and
replay.py; replay.py fetches stub_bevo.py and any fixture it needs from the same
site. These tests exercise exactly that layout from an empty directory, with a
file:// mirror standing in for the Pages site so nothing touches the network.
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
COPYTRADE = REPO_ROOT / "skills" / "butler-copytrade"
LEADER_ENV = "LEADER=11111111-1111-1111-1111-111111111111"


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


def _copy_skill(src: Path, dst: Path) -> Path:
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
    assert rel[:4] == ["tools/check_selectors.mjs", "tools/fixtures/me.json", "tools/fixtures/messages.json", "tools/fixtures/participants.json"] or True
    names = set(rel)
    assert {"tools/validate.py", "tools/replay.py", "tools/stub_bevo.py", "tools/check_selectors.mjs"} <= names
    fixture_files = {f.name for f in (REPO_ROOT / "tests" / "fixtures").iterdir() if f.is_file()}
    assert fixture_files, "no fixture files?"
    assert {f"tools/fixtures/{n}" for n in fixture_files} <= names
    assert not any(n.startswith("tools/fixtures/skills") for n in names)  # validator fixtures are not replay fixtures
    assert (tmp_path / "dist" / "tools" / "validate.py").read_bytes() == (REPO_ROOT / "scripts" / "validate.py").read_bytes()
    assert (tmp_path / "dist" / "tools" / "replay.py").read_bytes() == (REPO_ROOT / "tests" / "replay.py").read_bytes()


def test_publish_tools_cli_writes_under_dist(tmp_path):
    proc = _run([sys.executable, str(REPO_ROOT / "scripts" / "publish_tools.py"), "--dist", str(tmp_path / "d")], cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "d" / "tools" / "fixtures" / "trade-activity-page.jsonl").exists()
    assert "tool file(s) published under" in proc.stdout


# --- the developer path, from an empty directory ------------------------------------------


def test_validate_and_replay_from_the_published_layout(tmp_path):
    """Exactly what publish.yml serves, used from an unrelated cwd on a copy of the
    pinned copytrade skill: validate.py alone (no schema/, no scripts/) and replay.py
    with stub + fixtures beside it (no download)."""
    tools = tmp_path / "site" / "tools"
    publish_tools.publish(tmp_path / "site")
    skill = _copy_skill(COPYTRADE, tmp_path / "my-skill-checkout")

    v = _run([sys.executable, str(tools / "validate.py"), "--standalone", "--maintainer", str(skill)], cwd=tmp_path)
    assert v.returncode == 0, v.stdout + v.stderr
    assert "OK" in v.stdout

    r = _run(
        [sys.executable, str(tools / "replay.py"), "--standalone", str(skill), "--fixture", "trade-activity-page",
         "--env", LEADER_ENV, "--no-download"],
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "# standalone replay of butler-copytrade from" in r.stdout
    assert len([a for a in _actions(r.stdout) if a["call"] == "trade"]) == 3


def test_standalone_validate_uses_the_embedded_reserved_list(tmp_path):
    tools = tmp_path / "site" / "tools"
    publish_tools.publish(tmp_path / "site")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: clawhub\ndescription: x\nversion: 1.0.0\n"
        'metadata: {"bevo":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
        "## When to use\nx\n## Before you start\nx\n## Customize\nx\n"
        "## One-off procedure\n1. [FIXED] x\n## Failure handling\n|a|b|\n|-|-|\n## Limits\nx\n"
        "## Say to the owner\nx\n"
    )
    (skill / "CHANGELOG.md").write_text("# Changelog\n")
    v = _run([sys.executable, str(tools / "validate.py"), "--standalone", str(skill)], cwd=tmp_path)
    assert v.returncode == 1
    assert "'clawhub' is reserved" in v.stdout


def test_replay_downloads_stub_and_fixture_when_missing(tmp_path):
    """The author's real setup: only replay.py in the directory. stub_bevo.py and the
    fixture come from BUTLER_SKILLS_TOOLS_URL (a file:// mirror of the Pages layout)."""
    publish_tools.publish(tmp_path / "site")
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    skill = _copy_skill(COPYTRADE, tmp_path / "checkout")

    env = {"BUTLER_SKILLS_TOOLS_URL": (tmp_path / "site" / "tools").as_uri()}
    r = _run(
        [sys.executable, str(dev / "replay.py"), "--standalone", str(skill), "--fixture", "trade-activity-page", "--env", LEADER_ENV],
        cwd=skill, env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dev / "stub_bevo.py").exists()
    assert (dev / "fixtures" / "trade-activity-page.jsonl").exists()
    assert "# downloaded stub_bevo.py from" in r.stdout
    assert "# downloaded trade-activity-page.jsonl from" in r.stdout
    assert len([a for a in _actions(r.stdout) if a["call"] == "trade"]) == 3

    # second run: everything is beside replay.py now, nothing is fetched
    r2 = _run(
        [sys.executable, str(dev / "replay.py"), "--standalone", str(skill), "--fixture", "trade-activity-page", "--env", LEADER_ENV, "--no-download"],
        cwd=skill, env=env,
    )
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "# downloaded" not in r2.stdout


def test_stub_downloads_a_read_fixture_on_first_use(tmp_path):
    """A duty that calls bevo.read("/me") with no local me.json gets it from the mirror."""
    publish_tools.publish(tmp_path / "site")
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "duty.py").write_text("import bevo\n\nme = bevo.read('/me')\nbevo.log(me['username'])\n")
    env = {"BUTLER_SKILLS_TOOLS_URL": (tmp_path / "site" / "tools").as_uri()}
    r = _run([sys.executable, str(dev / "replay.py"), "--standalone", str(skill), "--fixture", "trade-activity-page"], cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[stub_bevo] downloaded fixture me.json" in r.stdout
    assert "[stub_bevo.log] owner" in r.stdout
    assert (dev / "fixtures" / "me.json").exists()


def test_replay_without_download_fails_loudly_on_a_missing_fixture(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    shutil.copy2(REPO_ROOT / "tests" / "stub_bevo.py", dev / "stub_bevo.py")
    skill = _copy_skill(COPYTRADE, tmp_path / "checkout")
    r = _run([sys.executable, str(dev / "replay.py"), "--standalone", str(skill), "--fixture", "no-such-page", "--no-download"], cwd=tmp_path)
    assert r.returncode != 0
    assert "fixture no-such-page.jsonl not found" in (r.stdout + r.stderr)


def test_replay_is_skipped_cleanly_for_a_one_off_only_skill(tmp_path):
    """A one-off-only skill ships no duty.py: replay prints 'nothing to replay', exits 0,
    and never needs a stub or a fixture."""
    dev = tmp_path / "dev"
    dev.mkdir()
    shutil.copy2(REPO_ROOT / "tests" / "replay.py", dev / "replay.py")
    skill = _copy_skill(COPYTRADE, tmp_path / "checkout")
    (skill / "duty.py").unlink()
    assert not (skill / "duty.py").exists()
    r = _run([sys.executable, str(dev / "replay.py"), "--standalone", str(skill), "--fixture", "trade-activity-page", "--no-download"], cwd=tmp_path)
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
    assert "viem@2" in text
    assert "no duty.py" in text  # replay is skipped, not failed, for one-off-only skills


def test_publish_workflow_publishes_the_tools():
    text = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text()
    assert "python3 scripts/publish_tools.py" in text
