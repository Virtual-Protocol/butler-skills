#!/usr/bin/env python3
"""replay.py — run a skill's duty.py offline against a captured fixture.

Usage:
    replay.py --standalone <dir> --fixture <name> [--env K=V ...] [--state-dir <dir>] [--fixtures-dir <dir>]
    tests/replay.py skills/<name> --fixture <name> [...]        # registry mode

`--standalone` is the skill-repo form. This file is self-contained: it looks for
`stub_bevo.py` and `fixtures/` next to itself — `tests/` in a registry checkout,
or wherever you downloaded it from — and whatever is missing there is downloaded
from the hub's Pages site, so an author's whole local setup is:

    curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
    python3 replay.py --standalone . --fixture trade-activity-page

BUTLER_SKILLS_TOOLS_URL overrides the download base (default
https://virtual-protocol.github.io/butler-skills/tools; `file:///...` works for
offline mirrors); --no-download forbids any fetch.

Puts stub_bevo.py on sys.path as the `bevo` module (recording semantics:
trade/execute/notify record their call instead of acting; read/rpc answer from
fixtures/*.json), runs duty.py's __main__ block in a working directory
(state_dir, default a fresh temp dir) so its state.json lands there, and prints
the recorded actions as JSON. A skill without a duty.py prints "nothing to
replay" and exits 0.

Exits 1 if any recorded trade/execute action lacks an idempotency key, or
if two actions share the same key.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import runpy
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TOOLS_URL = "https://virtual-protocol.github.io/butler-skills/tools"
TOOLS_URL = os.environ.get("BUTLER_SKILLS_TOOLS_URL", DEFAULT_TOOLS_URL).rstrip("/")


def download(url: str, dest: Path) -> bool:
    """Fetch url into dest; False (with a message) on any failure — the caller
    decides whether that is fatal."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"# could not download {url}: {e}")
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError as e:
        print(f"# could not write {dest}: {e}")
        return False
    print(f"# downloaded {dest.name} from {url}")
    return True


def ensure_stub(allow_download: bool) -> Path:
    """stub_bevo.py beside this file, downloading it there (or into a temp dir
    when this file's directory is not writable) when absent."""
    stub = HERE / "stub_bevo.py"
    if stub.exists():
        return stub
    if not allow_download:
        raise SystemExit(f"stub_bevo.py not found next to {Path(__file__).name} and --no-download given")
    url = f"{TOOLS_URL}/stub_bevo.py"
    if download(url, stub):
        return stub
    fallback = Path(tempfile.mkdtemp(prefix="butler-skills-tools-")) / "stub_bevo.py"
    if download(url, fallback):
        return fallback
    raise SystemExit(f"stub_bevo.py is not next to {Path(__file__).name} and could not be downloaded from {url}")


def ensure_fixture(fixtures_dir: Path, filename: str, allow_download: bool) -> Path:
    """The named fixture file under fixtures_dir, downloaded from the hub when
    absent. Fatal when it cannot be obtained — replaying zero events would be a
    silent false pass."""
    path = fixtures_dir / filename
    if path.exists():
        return path
    if allow_download and download(f"{TOOLS_URL}/fixtures/{filename}", path):
        return path
    raise SystemExit(
        f"fixture {filename} not found in {fixtures_dir}"
        + ("" if allow_download else " (--no-download given)")
        + f" — expected it there or at {TOOLS_URL}/fixtures/{filename}"
    )


def load_stub_bevo(stub_path: Path, fixture: str, fixtures_dir: Path, allow_download: bool):
    os.environ["BEVO_STUB_FIXTURE"] = fixture
    os.environ["BEVO_STUB_FIXTURES_DIR"] = str(fixtures_dir)
    # read()/rpc() fixtures are resolved lazily by name inside the stub; let it
    # fetch a missing one from the same place this file fetches from.
    os.environ["BEVO_STUB_FIXTURES_URL"] = f"{TOOLS_URL}/fixtures" if allow_download else ""
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
    parser.add_argument("--fixture", required=True, help="fixture basename under fixtures/, e.g. trade-activity-page")
    parser.add_argument("--fixtures-dir", default=str(HERE / "fixtures"), help="default: fixtures/ next to this file")
    parser.add_argument("--env", action="append", default=[], help="K=V, may repeat")
    parser.add_argument("--state-dir", default=None, help="working directory for duty.py's state.json (default: fresh temp dir)")
    parser.add_argument("--no-download", action="store_true", help="never fetch a missing stub_bevo.py or fixture")
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

    allow_download = not args.no_download
    fixtures_dir = Path(args.fixtures_dir).resolve()
    stub_path = ensure_stub(allow_download)
    ensure_fixture(fixtures_dir, f"{args.fixture}.jsonl", allow_download)
    stub = load_stub_bevo(stub_path, args.fixture, fixtures_dir, allow_download)

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
