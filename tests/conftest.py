"""conftest.py — fixtures shared by the tests that need a real skill tree.

No skill is checked out in this repository any more: skills.json records a
name, a GitHub link and a ref, and every build clones that. Most of the suite
is unaffected (synthetic skills in tmp_path), but three areas genuinely need
the real `butler-copytrade` files — the replay harness, the standalone-tools
developer path, and the README worked example — so they get them the way the
build does: one clone per session, through build_index.clone_skill().

That clone is the only place the suite touches the network. When it fails the
tests that use it are skipped, not failed: what they cover is this repo's
harness, not GitHub's reachability.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_index():
    spec = importlib.util.spec_from_file_location(
        "butler_skills_build_index_conftest", REPO_ROOT / "scripts" / "build_index.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_index = _load_build_index()


def registry_entry(name: str) -> dict:
    for row in build_index.load_registry():
        if row["name"] == name:
            return row
    raise AssertionError(f"{name} is not listed in skills.json")


@pytest.fixture(scope="session")
def copytrade_checkout(tmp_path_factory) -> Path:
    """The butler-copytrade repo, cloned at the ref skills.json follows."""
    entry = registry_entry("butler-copytrade")
    dest = tmp_path_factory.mktemp("registry-clones") / "butler-copytrade"
    try:
        build_index.clone_skill(entry, dest)
    except SystemExit as e:
        pytest.skip(f"cannot clone the butler-copytrade registry entry: {e}")
    return dest
