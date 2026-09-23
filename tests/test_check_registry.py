"""test_check_registry.py — scripts/check_registry.py against synthetic
templates.json and skills.json files. The registry is two listings, so these
are listing checks: valid names unique across both files, an
https://github.com/<owner>/<repo> URL with nothing smuggled into it, a plain
ref, and the ref resolving on the remote. The remote check is the only one
that needs the network; every test here either passes --offline or stubs
`ref_exists`, so the suite stays offline."""
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
    {"name": "tmpl-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"},
    {"name": "tmpl-beta", "repo": "https://github.com/someone/butler-skill-beta", "ref": "v1.2.0"},
]


def write_registry(tmp_path: Path, skills: list[dict]) -> Path:
    path = tmp_path / "templates.json"
    path.write_text(json.dumps({"templates": skills}, indent=2) + "\n")
    return path


def run(monkeypatch, tmp_path, skills, *argv, ref_exists=True, skill_rows=None) -> int:
    """Point the module at a synthetic templates.json (the `skills` argument — the
    rows predate the skill kind) plus skills.json (`skill_rows`, empty by default)
    and run its main().

    check_registry resolves REGISTRY_PATH at import time from its own
    location, so the file under test is swapped by monkeypatching that
    module attribute. `ref_exists` stubs the one network call: True/False to
    fix the answer, or a list to collect the (repo, ref) pairs it is asked
    about (proving --offline never asks)."""
    registry = write_registry(tmp_path, skills)
    skills_path = tmp_path / "skills.json"
    skills_path.write_text(json.dumps({"skills": skill_rows or []}, indent=2) + "\n")
    monkeypatch.setattr(check_registry, "REGISTRY_PATH", registry)
    monkeypatch.setattr(check_registry, "SKILLS_REGISTRY_PATH", skills_path)
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
    assert "OK    tmpl-alpha = https://github.com/someone/butler-skill-alpha @ main" in out
    assert "OK    tmpl-beta = https://github.com/someone/butler-skill-beta @ v1.2.0" in out


def test_bad_name_fails(monkeypatch, tmp_path, capsys):
    bad = [{"name": "Butler_Alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"}]
    assert run(monkeypatch, tmp_path, bad, "--offline") == 1
    assert "is not a valid template id" in capsys.readouterr().out


def test_duplicate_name_fails(monkeypatch, tmp_path, capsys):
    dupe = [
        {"name": "tmpl-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "main"},
        {"name": "tmpl-alpha", "repo": "https://github.com/someone/butler-skill-alpha-fork", "ref": "main"},
    ]
    assert run(monkeypatch, tmp_path, dupe, "--offline") == 1
    assert "tmpl-alpha: listed twice" in capsys.readouterr().out


def test_non_github_url_fails(monkeypatch, tmp_path, capsys):
    for repo in (
        "https://gitlab.com/someone/butler-skill-alpha",
        "http://github.com/someone/butler-skill-alpha",
        "git@github.com:someone/butler-skill-alpha.git",
    ):
        rows = [{"name": "tmpl-alpha", "repo": repo, "ref": "main"}]
        assert run(monkeypatch, tmp_path, rows, "--offline") == 1, repo
        assert "must be https://github.com/<owner>/<repo>" in capsys.readouterr().out


def test_url_with_credentials_or_query_fails(monkeypatch, tmp_path, capsys):
    for repo in (
        "https://user:token@github.com/someone/butler-skill-alpha",
        "https://github.com/someone/butler-skill-alpha?ref=evil",
        "https://github.com/someone/butler-skill-alpha#frag",
    ):
        rows = [{"name": "tmpl-alpha", "repo": repo, "ref": "main"}]
        assert run(monkeypatch, tmp_path, rows, "--offline") == 1, repo
        assert "must be https://github.com/<owner>/<repo>" in capsys.readouterr().out


def test_bad_ref_fails(monkeypatch, tmp_path, capsys):
    for ref in ("main branch", "main;rm -rf /", "--upload-pack=x", "v1.0.0" + "x" * 100):
        rows = [{"name": "tmpl-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": ref}]
        assert run(monkeypatch, tmp_path, rows, "--offline") == 1, ref
        assert "bad ref" in capsys.readouterr().out


def test_unsorted_registry_fails(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, list(reversed(GOOD)), "--offline") == 1
    assert "not sorted by name" in capsys.readouterr().out


def test_missing_templates_list_fails(monkeypatch, tmp_path, capsys):
    registry = tmp_path / "templates.json"
    registry.write_text(json.dumps({"comment": "no templates here"}) + "\n")
    monkeypatch.setattr(check_registry, "REGISTRY_PATH", registry)
    monkeypatch.setattr(sys, "argv", ["check_registry.py", "--offline"])
    assert check_registry.main() == 1
    assert "no `templates` list" in capsys.readouterr().out


def test_offline_skips_the_remote_check(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    assert run(monkeypatch, tmp_path, GOOD, "--offline", ref_exists=calls) == 0
    assert calls == [], "--offline must not touch the network"


def test_online_checks_every_ref_on_the_remote(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    assert run(monkeypatch, tmp_path, GOOD, ref_exists=calls) == 0
    assert calls == [(s["repo"], s["ref"]) for s in GOOD]


def test_ref_that_does_not_resolve_fails(monkeypatch, tmp_path, capsys):
    rows = [{"name": "tmpl-alpha", "repo": "https://github.com/someone/butler-skill-alpha", "ref": "no-such-ref"}]
    assert run(monkeypatch, tmp_path, rows, ref_exists=False) == 1
    assert "has no ref 'no-such-ref'" in capsys.readouterr().out


def test_real_registry_listing_passes_offline(monkeypatch, capsys):
    """The registry actually checked in here, listing checks only."""
    monkeypatch.setattr(sys, "argv", ["check_registry.py", "--offline"])
    assert check_registry.main() == 0, capsys.readouterr().out


# --- skills.json ------------------------------------------------------------------------------

SKILLS = [
    {"name": "butler-app-checkout", "repo": "https://github.com/Virtual-Protocol/butler-skill-app-checkout", "ref": "main"},
    {"name": "zeta", "repo": "https://github.com/someone/butler-skill-zeta", "ref": "v1.0.0"},
]


def test_a_valid_skills_listing_passes(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, GOOD, "--offline", skill_rows=SKILLS) == 0
    out = capsys.readouterr().out
    assert "OK    butler-app-checkout = https://github.com/Virtual-Protocol/butler-skill-app-checkout @ main" in out
    assert "OK    tmpl-alpha = " in out


def test_a_skill_name_mastra_would_drop_fails(monkeypatch, tmp_path, capsys):
    for name in ("foo--bar", "Foo", "foo-", "foo_bar", "a" * 65):
        rows = [{"name": name, "repo": "https://github.com/someone/butler-skill-x", "ref": "main"}]
        assert run(monkeypatch, tmp_path, GOOD, "--offline", skill_rows=rows) == 1, name
        assert "is not a valid skill name" in capsys.readouterr().out


def test_one_name_is_one_kind_across_both_files(monkeypatch, tmp_path, capsys):
    rows = [{"name": "tmpl-alpha", "repo": "https://github.com/someone/butler-skill-alpha-skill", "ref": "main"}]
    assert run(monkeypatch, tmp_path, GOOD, "--offline", skill_rows=rows) == 1
    assert "tmpl-alpha: listed in both templates.json and skills.json" in capsys.readouterr().out


def test_skills_json_gets_the_same_listing_rules(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, GOOD, "--offline", skill_rows=list(reversed(SKILLS))) == 1
    assert "skills.json is not sorted by name" in capsys.readouterr().out
    rows = [{"name": "zeta", "repo": "https://gitlab.com/someone/zeta", "ref": "main"}]
    assert run(monkeypatch, tmp_path, GOOD, "--offline", skill_rows=rows) == 1
    assert "zeta: repo must be https://github.com/<owner>/<repo>" in capsys.readouterr().out
    rows = [{"name": "zeta", "repo": "https://github.com/someone/zeta", "ref": "main;rm -rf /"}]
    assert run(monkeypatch, tmp_path, GOOD, "--offline", skill_rows=rows) == 1
    assert "zeta: bad ref" in capsys.readouterr().out

    (tmp_path / "skills.json").write_text(json.dumps({"comment": "no skills key"}))
    monkeypatch.setattr(sys, "argv", ["check_registry.py", "--offline"])
    assert check_registry.main() == 1
    assert "skills.json has no `skills` list" in capsys.readouterr().out


def test_online_checks_skill_refs_too(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    assert run(monkeypatch, tmp_path, GOOD, ref_exists=calls, skill_rows=SKILLS) == 0
    assert calls == [(s["repo"], s["ref"]) for s in GOOD + SKILLS]
