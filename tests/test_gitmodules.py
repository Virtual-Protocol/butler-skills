"""test_gitmodules.py — the registry's .gitmodules is the trust boundary:
every submodule is a skills/<name> path with an https://github.com/<owner>/<repo>
URL, the pinned checkout's frontmatter name equals <name>, and the pin (the
gitlink in HEAD) is what the checkout sits on."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITMODULES = REPO_ROOT / ".gitmodules"

HTTPS_GITHUB_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def parse_gitmodules(text: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'^\[submodule\s+"(.+)"\]$', line)
        if m:
            current = {"name": m.group(1)}
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*)\s*=\s*(.*)$", line)
        if m and current is not None:
            current[m.group(1)] = m.group(2).strip()
            if m.group(1) == "path":
                entries[current["path"]] = current
    return entries


def frontmatter_name(skill_md: Path) -> str | None:
    for line in skill_md.read_text().splitlines()[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def test_gitmodules_exists_and_declares_skills():
    assert GITMODULES.exists()
    entries = parse_gitmodules(GITMODULES.read_text())
    assert entries, ".gitmodules declares no submodules"


def test_every_submodule_is_a_skill_path_with_https_github_url():
    entries = parse_gitmodules(GITMODULES.read_text())
    for path, entry in entries.items():
        assert path.startswith("skills/"), f"{path}: submodules live only under skills/"
        name = path[len("skills/"):]
        assert "/" not in name and NAME_RE.match(name), f"{path}: not skills/<valid-name>"
        url = entry.get("url", "")
        assert HTTPS_GITHUB_RE.match(url), f"{path}: url {url!r} must be https://github.com/<owner>/<repo>"
        assert not url.endswith(".git"), f"{path}: record the plain repo URL, not {url!r}"
        assert "branch" not in entry, f"{path}: pins are commits, never a floating branch"


def test_every_submodule_matches_git_config_view():
    """The hand parser above agrees with git's own reading of the file."""
    out = subprocess.run(
        ["git", "config", "-f", str(GITMODULES), "--get-regexp", r"^submodule\..*\.(path|url)$"],
        capture_output=True, text=True, check=True, cwd=str(REPO_ROOT),
    ).stdout
    seen: dict[str, dict] = {}
    for line in out.splitlines():
        key, value = line.split(" ", 1)
        _, name, field = key.rsplit(".", 2)
        seen.setdefault(name, {})[field] = value
    ours = parse_gitmodules(GITMODULES.read_text())
    assert {v["path"]: v["url"] for v in seen.values()} == {p: e["url"] for p, e in ours.items()}


def test_pinned_checkout_name_and_gitlink_agree():
    entries = parse_gitmodules(GITMODULES.read_text())
    for path in entries:
        d = REPO_ROOT / path
        assert (d / "SKILL.md").exists(), f"{path} not initialised — run git submodule update --init --recursive"
        assert frontmatter_name(d / "SKILL.md") == path[len("skills/"):]
        head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        gitlink = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-tree", "HEAD", path], capture_output=True, text=True, check=True
        ).stdout.split()
        assert gitlink[:2] == ["160000", "commit"], f"{path} is not a gitlink in HEAD: {gitlink}"
        assert gitlink[2] == head, f"{path}: checkout {head} differs from the pinned {gitlink[2]}"


def test_no_vendored_skill_directories_remain():
    entries = parse_gitmodules(GITMODULES.read_text())
    for d in (REPO_ROOT / "skills").iterdir():
        if d.is_dir() and not d.name.startswith(".") and d.name != "__pycache__":
            assert f"skills/{d.name}" in entries, f"skills/{d.name} is a plain directory, not a submodule"
    assert not (REPO_ROOT / "skills" / "_template").exists()
