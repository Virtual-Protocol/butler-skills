"""test_new_skill.py — scripts/new_skill.py prints the git-backed flow for a
duty template (recipe.json/duty.py/README.md at a repo's root; the PR is one
templates.json entry, not a submodule pin) and keeps the reserved-id gate."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "new_skill.py"


def run(*args: str):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=str(REPO_ROOT))


def test_prints_create_validate_and_registry_entry_steps():
    proc = run("my-dca", "--owner", "alice")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "gh repo create alice/butler-skill-my-dca --public --clone" in out
    assert "curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py" in out
    assert "curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py" in out
    assert "python3 validate.py --standalone ." in out
    assert "python3 replay.py --standalone . --fixture trade-activity-page" in out
    assert "git clone" not in out  # nobody is told to clone the registry
    assert "uses: Virtual-Protocol/butler-skills/.github/actions/validate@main" in out
    # Step 3 is one line in templates.json, not a submodule pin.
    assert '{"name": "my-dca", "repo": "https://github.com/alice/butler-skill-my-dca", "ref": "main"}' in out
    assert "gh pr create --repo Virtual-Protocol/butler-skills --base main" in out
    assert "submodule" not in out
    assert "with no review in the registry" in out
    assert "You never open another PR here unless the" in out and "template is removed." in out
    assert not (REPO_ROOT / "templates").exists()  # nothing is checked out in this repo


def test_butler_prefix_strips_to_repo_name_with_maintainer():
    proc = run("butler-thing", "--maintainer")
    assert proc.returncode == 0, proc.stderr
    assert "butler-skill-thing" in proc.stdout
    assert '"name": "butler-thing"' in proc.stdout


def test_reserved_id_and_prefixes_are_refused():
    assert run("clawhub").returncode != 0
    assert run("butler-thing").returncode != 0  # maintainer-only without --maintainer
    assert run("Bad_Name").returncode != 0
    # bevo-* is the container's bundled-command namespace: refused even with --maintainer
    proc = run("bevo-thing", "--maintainer")
    assert proc.returncode != 0
    assert "bundled-command" in proc.stderr


if __name__ == "__main__":
    sys.exit(0)
