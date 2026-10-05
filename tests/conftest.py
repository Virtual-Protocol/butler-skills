"""conftest.py — fixtures shared across the suite.

No template or skill repo is checked out in this repository: templates.json
and skills.json only link to them, and this suite never clones a registry
entry over the network. Wherever a test needs "a real template on disk" it
gets a copy of `tests/fixtures/templates/valid`, and "a real skill on disk" a
copy of `tests/fixtures/skills/valid` — the fully-compliant local fixtures,
which exercise the real validate.py / build_index.py / replay.py code paths
without any network dependency. Registry-mode tests that need a clone build a
throwaway git repo in tmp_path instead.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID_TEMPLATE = REPO_ROOT / "tests" / "fixtures" / "templates" / "valid"


@pytest.fixture
def template_checkout(tmp_path) -> Path:
    """A copy of the local `valid` fixture template, named after itself —
    the shape clone_registry_templates() produces for a real registry entry."""
    dest = tmp_path / "valid"
    shutil.copytree(VALID_TEMPLATE, dest)
    return dest
