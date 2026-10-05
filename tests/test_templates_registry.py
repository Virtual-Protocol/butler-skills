"""test_templates_registry.py — templates.json is the registry, and it is the
whole trust boundary that lives in this repo: one entry per duty template, a
name and an https://github.com/<owner>/<repo> link, plus the ref the build
resolves. There is no pinned tree here any more, so what this file can check
is the listing — that it is well formed, sorted, free of duplicates, and that
build_index.load_registry() reads back exactly what is written down."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "templates.json"

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


def raw_templates() -> list[dict]:
    return json.loads(REGISTRY_PATH.read_text())["templates"]


def test_registry_exists_and_is_well_formed():
    """The registry file must exist and carry a `templates` list.

    It may legitimately be EMPTY — at the v3 cutover it is, because CI clones
    and validates every listed repo, so a repo may only be listed once it
    actually ships a bundle. An empty registry publishes an empty index, and a
    container reading one answers "no such template", which is the same answer
    it gives for a query nothing matches.
    """
    assert REGISTRY_PATH.exists(), "templates.json is the registry — it must exist"
    assert isinstance(raw_templates(), list), "templates.json has no `templates` list"


def test_every_entry_is_a_name_and_an_https_github_link():
    for row in raw_templates():
        assert set(row) <= {"name", "repo", "ref"}, f"{row}: unexpected key"
        name, repo, ref = row.get("name"), row.get("repo"), row.get("ref")
        assert NAME_RE.match(str(name)), f"{name!r} is not a valid template id"
        assert HTTPS_GITHUB_RE.match(str(repo)), f"{name}: repo {repo!r} must be https://github.com/<owner>/<repo>"
        assert not str(repo).endswith(".git"), f"{name}: record the plain repo URL, not {repo!r}"
        assert REF_RE.match(str(ref)), f"{name}: ref {ref!r} must be a plain branch or tag name"


def test_names_are_unique_and_sorted():
    names = [row["name"] for row in raw_templates()]
    assert len(set(names)) == len(names), f"duplicate template id in templates.json: {names}"
    assert names == sorted(names), f"templates.json is not sorted by name: {names}"


def test_load_registry_agrees_with_the_file():
    """The build reads the registry through load_registry(); it must return
    the file, not a filtered or reordered view of it."""
    assert build_index.load_registry() == raw_templates()


def test_skills_listing_is_well_formed():
    """skills.json is the other half of the registry — the same shape, Mastra's name
    rule, and no name that templates.json also lists."""
    path = REPO_ROOT / "skills.json"
    rows = json.loads(path.read_text())["skills"]
    assert isinstance(rows, list)
    assert build_index.load_skills_registry() == rows
    names = [row["name"] for row in rows]
    assert names == sorted(set(names))
    assert not set(names) & {row["name"] for row in raw_templates()}
    for row in rows:
        assert set(row) <= {"name", "repo", "ref"}
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", row["name"]) and len(row["name"]) <= 64
        assert HTTPS_GITHUB_RE.match(row["repo"]) and REF_RE.match(row["ref"])


def test_no_submodule_machinery_remains():
    """Templates are links now: nothing is checked out here, so a leftover
    .gitmodules or templates/ tree would be a second, stale source of truth."""
    assert not (REPO_ROOT / ".gitmodules").exists(), ".gitmodules is gone — the registry is templates.json"
    assert not (REPO_ROOT / "skills").exists(), "skills/ is gone — each build clones from templates.json"
    assert not (REPO_ROOT / "templates").exists(), "templates/ is gone — each build clones from templates.json"
