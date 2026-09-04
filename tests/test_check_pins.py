"""test_check_pins.py — scripts/check_pins.py against a synthetic registry:
a local skill repo with a v1.0.0 tag, cloned into <root>/skills/<name> and
declared in <root>/.gitmodules (a real submodule is not needed — the checker
only reads .gitmodules and runs git inside the checkout). Runs offline
(--no-fetch)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_check_pins():
    spec = importlib.util.spec_from_file_location("butler_skills_check_pins", REPO_ROOT / "scripts" / "check_pins.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_pins = _load_check_pins()

SKILL_MD = (
    "---\nname: foo\ndescription: x\nversion: 1.0.0\n"
    'metadata: {"butler":{"tier":"on-demand","modes":["one-off"],"moneyMoving":false}}\n---\n\n'
    "## When to use\nx\n"
)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env=GIT_ENV).stdout


def make_skill_repo(path: Path, version: str = "1.0.0", tag: str | None = "v1.0.0") -> Path:
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    (path / "SKILL.md").write_text(SKILL_MD.replace("version: 1.0.0", f"version: {version}"))
    (path / "CHANGELOG.md").write_text("# Changelog\n")
    git("add", "SKILL.md", "CHANGELOG.md", cwd=path)
    git("commit", "-q", "-m", "init", cwd=path)
    if tag:
        git("tag", "-a", tag, "-m", tag, cwd=path)
    return path


def make_registry(tmp_path: Path, url: str = "https://github.com/someone/butler-skill-foo", **skill_kwargs) -> Path:
    upstream = make_skill_repo(tmp_path / "upstream", **skill_kwargs)
    root = tmp_path / "registry"
    (root / "skills").mkdir(parents=True)
    git("clone", "-q", str(upstream), str(root / "skills" / "foo"), cwd=tmp_path)
    (root / ".gitmodules").write_text(f'[submodule "skills/foo"]\n\tpath = skills/foo\n\turl = {url}\n')
    return root


def test_pinned_at_tag_passes(tmp_path, capsys):
    root = make_registry(tmp_path)
    assert check_pins.run(root, fetch=False) == 0
    out = capsys.readouterr().out
    assert "OK    skills/foo = https://github.com/someone/butler-skill-foo @" in out
    assert "(v1.0.0)" in out


def test_pinned_commit_without_the_version_tag_fails(tmp_path, capsys):
    root = make_registry(tmp_path, tag=None)
    assert check_pins.run(root, fetch=False) == 1
    assert "does not carry tag v1.0.0" in capsys.readouterr().out


def test_tag_must_match_frontmatter_version(tmp_path, capsys):
    # tagged v1.0.0 but the pinned SKILL.md says 1.1.0 (a moved pointer without a matching tag)
    root = make_registry(tmp_path, version="1.1.0", tag="v1.0.0")
    assert check_pins.run(root, fetch=False) == 1
    assert "does not carry tag v1.1.0" in capsys.readouterr().out


def test_non_https_github_url_fails(tmp_path, capsys):
    root = make_registry(tmp_path, url="git@github.com:someone/butler-skill-foo.git")
    assert check_pins.run(root, fetch=False) == 1
    assert "must be https://github.com/<owner>/<repo>" in capsys.readouterr().out


def test_symlink_in_pinned_checkout_fails(tmp_path, capsys):
    root = make_registry(tmp_path)
    os.symlink(root / "skills" / "foo" / "SKILL.md", root / "skills" / "foo" / "link.md")
    assert check_pins.run(root, fetch=False) == 1
    assert "symlink: link.md" in capsys.readouterr().out


def test_nested_submodule_in_pinned_checkout_fails(tmp_path, capsys):
    root = make_registry(tmp_path)
    (root / "skills" / "foo" / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = https://github.com/a/b\n')
    (root / "skills" / "foo" / "x" / ".git").mkdir(parents=True)
    assert check_pins.run(root, fetch=False) == 1
    out = capsys.readouterr().out
    assert "nested submodules: .gitmodules" in out
    assert "nested git repository: x/.git" in out


def test_path_name_must_equal_frontmatter_name(tmp_path, capsys):
    root = make_registry(tmp_path)
    (root / "skills" / "foo").rename(root / "skills" / "bar")
    (root / ".gitmodules").write_text('[submodule "skills/bar"]\n\tpath = skills/bar\n\turl = https://github.com/someone/butler-skill-foo\n')
    assert check_pins.run(root, fetch=False) == 1
    assert "frontmatter name 'foo' must equal the submodule path name 'bar'" in capsys.readouterr().out


def test_uninitialised_and_vendored_dirs_fail(tmp_path, capsys):
    root = tmp_path / "registry"
    (root / "skills" / "foo").mkdir(parents=True)  # declared, but empty (not initialised)
    (root / "skills" / "vendored").mkdir()  # not declared at all
    (root / "skills" / "vendored" / "SKILL.md").write_text(SKILL_MD)
    (root / ".gitmodules").write_text('[submodule "skills/foo"]\n\tpath = skills/foo\n\turl = https://github.com/someone/butler-skill-foo\n')
    assert check_pins.run(root, fetch=False) == 1
    out = capsys.readouterr().out
    assert "skills/foo: not initialised" in out
    assert "skills/vendored: not a submodule" in out


@pytest.mark.skipif(not (REPO_ROOT / ".gitmodules").exists(), reason="no .gitmodules")
def test_real_registry_pins_are_at_their_tags_offline():
    """The real pins, using the tags already present in the local submodule
    checkouts (no network)."""
    assert check_pins.run(REPO_ROOT, fetch=False) == 0
