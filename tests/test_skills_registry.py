"""test_skills_registry.py — skills.json is the registry, and it is the whole
trust boundary that lives in this repo: one entry per skill, a name and an
https://github.com/<owner>/<repo> link, plus the ref the build resolves. There
is no pinned tree here any more, so what this file can check is the listing —
that it is well formed, sorted, free of duplicates, and that
build_index.load_registry() reads back exactly what is written down."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "skills.json"

HTTPS_GITHUB_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


def _load_build_index_module():
    spec = importlib.util.spec_from_file_location(
        "butler_skills_build_index", REPO_ROOT / "scripts" / "build_index.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_index = _load_build_index_module()


def raw_skills() -> list[dict]:
    return json.loads(REGISTRY_PATH.read_text())["skills"]


def test_registry_exists_and_lists_skills():
    assert REGISTRY_PATH.exists(), "skills.json is the registry — it must exist"
    rows = raw_skills()
    assert rows, "skills.json lists no skills"


def test_every_entry_is_a_name_and_an_https_github_link():
    for row in raw_skills():
        assert set(row) <= {"name", "repo", "ref"}, f"{row}: unexpected key"
        name, repo, ref = row.get("name"), row.get("repo"), row.get("ref")
        assert NAME_RE.match(str(name)), f"{name!r} is not a valid skill name"
        assert HTTPS_GITHUB_RE.match(str(repo)), f"{name}: repo {repo!r} must be https://github.com/<owner>/<repo>"
        assert not str(repo).endswith(".git"), f"{name}: record the plain repo URL, not {repo!r}"
        assert REF_RE.match(str(ref)), f"{name}: ref {ref!r} must be a plain branch or tag name"


def test_names_are_unique_and_sorted():
    names = [row["name"] for row in raw_skills()]
    assert len(set(names)) == len(names), f"duplicate skill name in skills.json: {names}"
    assert names == sorted(names), f"skills.json is not sorted by name: {names}"


def test_load_registry_agrees_with_the_file():
    """The build reads the registry through load_registry(); it must return the
    file, not a filtered or reordered view of it."""
    assert build_index.load_registry() == raw_skills()


def test_no_submodule_machinery_remains():
    """Skills are links now: nothing is checked out here, so a leftover
    .gitmodules or skills/ tree would be a second, stale source of truth."""
    assert not (REPO_ROOT / ".gitmodules").exists(), ".gitmodules is gone — the registry is skills.json"
    assert not (REPO_ROOT / "skills").exists(), "skills/ is gone — each build clones from skills.json"
