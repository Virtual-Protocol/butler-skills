"""test_check_registry.py — scripts/check_registry.py against synthetic
skills.json files. The registry is a listing, so these are listing checks:
valid unique names, an https://github.com/<owner>/<repo> URL with nothing
smuggled into it, a plain ref, and the ref resolving on the remote. The
remote check is the only one that needs the network; every test here either
passes --offline or stubs `ref_exists`, so the suite stays offline."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_check_registry():
    spec = importlib.util.spec_from_file_location(
        "butler_skills_check_registry", REPO_ROOT / "scripts" / "check_registry.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_registry = _load_check_registry()

GOOD = [
    {"name": "butler-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"},
    {"name": "butler-beta", "repo": "https://github.com/someone/butler-skill-beta", "ref": "v1.2.0"},
]


def write_registry(tmp_path: Path, skills: list[dict]) -> Path:
    path = tmp_path / "skills.json"
    path.write_text(json.dumps({"skills": skills}, indent=2) + "\n")
    return path


def run(monkeypatch, tmp_path, skills, *argv, ref_exists=True) -> int:
    """Point the module at a synthetic skills.json and run its main().

    check_registry resolves REGISTRY_PATH at import time from its own
    location, so the file under test is swapped by monkeypatching that
    module attribute. `ref_exists` stubs the one network call: True/False to
    fix the answer, or a list to collect the (repo, ref) pairs it is asked
    about (proving --offline never asks)."""
    registry = write_registry(tmp_path, skills)
    monkeypatch.setattr(check_registry, "REGISTRY_PATH", registry)
    monkeypatch.setattr(sys, "argv", ["check_registry.py", *argv])
    if isinstance(ref_exists, list):
        calls = ref_exists
        monkeypatch.setattr(check_registry, "ref_exists", lambda repo, ref: calls.append((repo, ref)) or True)
    else:
        monkeypatch.setattr(check_registry, "ref_exists", lambda repo, ref: ref_exists)
    return check_registry.main()


def test_valid_registry_passes(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, GOOD, "--offline") == 0
    out = capsys.readouterr().out
    assert "OK    butler-alpha = https://github.com/someone/butler-skill-alpha @ main" in out
    assert "OK    butler-beta = https://github.com/someone/butler-skill-beta @ v1.2.0" in out


def test_bad_name_fails(monkeypatch, tmp_path, capsys):
    bad = [{"name": "Butler_Alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"}]
    assert run(monkeypatch, tmp_path, bad, "--offline") == 1
    assert "is not a valid skill name" in capsys.readouterr().out


def test_duplicate_name_fails(monkeypatch, tmp_path, capsys):
    dupe = [
        {"name": "butler-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"},
        {"name": "butler-alpha", "repo": "https://github.com/someone/butler-skill-alpha-fork", "ref": "main"},
    ]
    assert run(monkeypatch, tmp_path, dupe, "--offline") == 1
    assert "butler-alpha: listed twice" in capsys.readouterr().out


def test_non_github_url_fails(monkeypatch, tmp_path, capsys):
    for repo in (
        "https://gitlab.com/someone/butler-skill-alpha",
        "http://github.com/someone/butler-skill-alpha",
        "git@github.com:someone/butler-skill-alpha.git",
    ):
        rows = [{"name": "butler-alpha", "repo": repo, "ref": "main"}]
        assert run(monkeypatch, tmp_path, rows, "--offline") == 1, repo
        assert "must be https://github.com/<owner>/<repo>" in capsys.readouterr().out


def test_url_with_credentials_or_query_fails(monkeypatch, tmp_path, capsys):
    for repo in (
        "https://user:token@github.com/someone/butler-skill-alpha",
        "https://github.com/someone/butler-skill-alpha?ref=evil",
        "https://github.com/someone/butler-skill-alpha#frag",
    ):
        rows = [{"name": "butler-alpha", "repo": repo, "ref": "main"}]
        assert run(monkeypatch, tmp_path, rows, "--offline") == 1, repo
        assert "must be https://github.com/<owner>/<repo>" in capsys.readouterr().out


def test_bad_ref_fails(monkeypatch, tmp_path, capsys):
    for ref in ("main branch", "main;rm -rf /", "--upload-pack=x", "v1.0.0" + "x" * 100):
        rows = [{"name": "butler-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": ref}]
        assert run(monkeypatch, tmp_path, rows, "--offline") == 1, ref
        assert "bad ref" in capsys.readouterr().out


def test_unsorted_registry_fails(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, list(reversed(GOOD)), "--offline") == 1
    assert "not sorted by name" in capsys.readouterr().out


def test_missing_skills_list_fails(monkeypatch, tmp_path, capsys):
    registry = tmp_path / "skills.json"
    registry.write_text(json.dumps({"comment": "no skills here"}) + "\n")
    monkeypatch.setattr(check_registry, "REGISTRY_PATH", registry)
    monkeypatch.setattr(sys, "argv", ["check_registry.py", "--offline"])
    assert check_registry.main() == 1
    assert "no `skills` list" in capsys.readouterr().out


def test_offline_skips_the_remote_check(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    assert run(monkeypatch, tmp_path, GOOD, "--offline", ref_exists=calls) == 0
    assert calls == [], "--offline must not touch the network"


def test_online_checks_every_ref_on_the_remote(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    assert run(monkeypatch, tmp_path, GOOD, ref_exists=calls) == 0
    assert calls == [(s["repo"], s["ref"]) for s in GOOD]


def test_ref_that_does_not_resolve_fails(monkeypatch, tmp_path, capsys):
    rows = [{"name": "butler-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "no-such-ref"}]
    assert run(monkeypatch, tmp_path, rows, ref_exists=False) == 1
    assert "has no ref 'no-such-ref'" in capsys.readouterr().out


def test_real_registry_listing_passes_offline(monkeypatch, capsys):
    """The registry actually checked in here, listing checks only."""
    monkeypatch.setattr(sys, "argv", ["check_registry.py", "--offline"])
    assert check_registry.main() == 0, capsys.readouterr().out
