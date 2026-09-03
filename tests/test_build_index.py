"""test_build_index.py — pytest coverage for scripts/build_index.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_index_module():
    spec = importlib.util.spec_from_file_location("butler_skills_build_index", REPO_ROOT / "scripts" / "build_index.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_index = _load_build_index_module()


def test_collect_skill_bevo_copytrade():
    entry = build_index.collect_skill(REPO_ROOT / "skills" / "bevo-copytrade", yanked=set())
    assert entry["name"] == "bevo-copytrade"
    assert entry["version"] == "1.0.0"
    assert entry["tier"] == "on-demand"
    assert "one-off" in entry["modes"] and "duty" in entry["modes"]
    assert entry["moneyMoving"] is True
    assert entry["yanked"] is False
    paths = {f["path"] for f in entry["files"]}
    assert {"SKILL.md", "duty.py", "CHANGELOG.md"} <= paths
    for f in entry["files"]:
        assert len(f["sha256"]) == 64
        assert f["bytes"] > 0


def test_collect_skill_respects_yanked():
    entry = build_index.collect_skill(
        REPO_ROOT / "skills" / "bevo-copytrade", yanked={"bevo-copytrade@1.0.0"}
    )
    assert entry["yanked"] is True


def test_regenerate_catalog_contains_all_skills():
    entries = [
        build_index.collect_skill(d, yanked=set())
        for d in sorted((REPO_ROOT / "skills").iterdir())
        if d.is_dir() and d.name != "_template" and (d / "SKILL.md").exists()
    ]
    catalog = build_index.regenerate_catalog(entries)
    for e in entries:
        assert e["name"] in catalog


def test_dry_run_full_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "build_index.py"), "--dry-run", "--channel", "canary"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skill(s) indexed for channel=canary" in proc.stdout
