"""conftest.py — fixtures shared across the suite.

No template repo is checked out in this repository, and the four templates
templates.json currently lists (app-checkout, copytrade, dca, web-checkout)
have not yet been converted from the retired SKILL.md shape to the
recipe.json/duty.py/README.md bundle this registry now requires — that
conversion is tracked separately. So this suite never clones a registry
entry over the network; wherever the old suite needed "a real template on
disk", it now gets a copy of `tests/fixtures/templates/valid`, the
fully-compliant local fixture, which exercises the real validate.py /
build_index.py / replay.py code paths without any network dependency.
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
