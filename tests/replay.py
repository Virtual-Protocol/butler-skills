#!/usr/bin/env python3
"""replay.py — run a skill's duty.py offline against a captured fixture.

Usage:
    tests/replay.py skills/<name> --fixture <name> [--env K=V ...] [--state-dir <dir>] [--fixtures-dir <dir>]
    tests/replay.py --standalone <dir> --fixture <name> [...]   # any directory holding a duty.py

`--standalone` is the skill-repo form: run it from your own repository checkout
(the template's CI does exactly this after cloning butler-skills for the stub
and fixtures). The fixtures always resolve relative to this checkout of
butler-skills, not to the current directory, so it works from anywhere.

Puts tests/stub_bevo.py on sys.path as the `bevo` module (recording
semantics: trade/execute/notify record their call instead of acting;
read/rpc answer from tests/fixtures/*.json), runs duty.py's __main__ block
in a working directory (state_dir, default a fresh temp dir) so its
state.json lands there, and prints the recorded actions as JSON.

Exits 1 if any recorded trade/execute action lacks an idempotency key, or
if two actions share the same key.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import runpy
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_stub_bevo(fixture: str, fixtures_dir: Path):
    stub_path = REPO_ROOT / "tests" / "stub_bevo.py"
    os.environ["BEVO_STUB_FIXTURE"] = fixture
    os.environ["BEVO_STUB_FIXTURES_DIR"] = str(fixtures_dir)
    spec = importlib.util.spec_from_file_location("bevo", stub_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    sys.modules["bevo"] = module
    return module


def skill_name_of(skill_dir: Path) -> str | None:
    """Frontmatter `name:` of skill_dir/SKILL.md, or None — informational only."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    for line in skill_md.read_text().splitlines()[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a skill's duty.py offline against a fixture.")
    parser.add_argument("skill", help="skills/<name> path, or with --standalone any directory that holds a duty.py")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="the path is a skill repository checkout (name from SKILL.md's frontmatter, not the directory)",
    )
    parser.add_argument("--fixture", required=True, help="fixture basename under tests/fixtures/, e.g. trade-activity-page")
    parser.add_argument("--fixtures-dir", default=str(REPO_ROOT / "tests" / "fixtures"))
    parser.add_argument("--env", action="append", default=[], help="K=V, may repeat")
    parser.add_argument("--state-dir", default=None, help="working directory for duty.py's state.json (default: fresh temp dir)")
    args = parser.parse_args()

    skill_dir = Path(args.skill).resolve()
    if not skill_dir.is_dir():
        parser.error(f"{skill_dir} is not a directory")
    duty_path = skill_dir / "duty.py"
    if not duty_path.exists():
        print(f"no duty.py in {skill_dir} — nothing to replay")
        return 0
    if args.standalone:
        print(f"# standalone replay of {skill_name_of(skill_dir) or skill_dir.name} from {skill_dir}")

    for kv in args.env:
        if "=" not in kv:
            parser.error(f"--env expects K=V, got {kv!r}")
        k, v = kv.split("=", 1)
        os.environ[k] = v

    state_dir = Path(args.state_dir) if args.state_dir else Path(tempfile.mkdtemp(prefix="butler-skills-replay-"))
    state_dir.mkdir(parents=True, exist_ok=True)

    stub = load_stub_bevo(args.fixture, Path(args.fixtures_dir))

    cwd = os.getcwd()
    os.chdir(state_dir)
    try:
        runpy.run_path(str(duty_path), run_name="__main__")
    finally:
        os.chdir(cwd)

    actions = stub.RECORDED_ACTIONS
    ok = True
    seen_keys: dict[str, str] = {}
    for action in actions:
        if action.get("call") in ("trade", "execute"):
            key = action.get("key")
            if not key:
                print(f"ERROR: {action['call']} action missing an idempotency key: {action}")
                ok = False
                continue
            if key in seen_keys:
                print(f"ERROR: duplicate idempotency key {key!r} used by {seen_keys[key]} and {action['call']}")
                ok = False
            seen_keys[key] = action["call"]

    print("# ACTIONS_JSON_START")
    print(json.dumps(actions, indent=2, default=str))
    print("# ACTIONS_JSON_END")
    print(f"# state dir: {state_dir}")
    print(f"# {len(actions)} action(s) recorded")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
