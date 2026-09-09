#!/usr/bin/env python3
"""publish_tools.py — lay out the standalone developer tools under dist/tools/.

Usage:
    scripts/publish_tools.py [--dist <dir>]      # default: <repo>/dist

publish.yml runs this after building the index, so the Pages site serves, next to
stable/ and canary/:

    tools/validate.py            <- scripts/validate.py        (single-file validator)
    tools/replay.py              <- tests/replay.py            (offline replay harness)
    tools/stub_bevo.py           <- tests/stub_bevo.py         (the `bevo` stand-in replay.py loads)
    tools/check_selectors.mjs    <- scripts/check_selectors.mjs
    tools/fixtures/<file>        <- every regular file directly under tests/fixtures/

A skill author needs only the first two:

    curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
    curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py

replay.py downloads stub_bevo.py and any fixture it needs from this same layout when
they are not already next to it. Nobody has to clone the registry to validate a skill.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TOOL_FILES: list[tuple[str, str]] = [
    ("scripts/validate.py", "validate.py"),
    ("tests/replay.py", "replay.py"),
    ("tests/stub_bevo.py", "stub_bevo.py"),
    ("scripts/check_selectors.mjs", "check_selectors.mjs"),
]
FIXTURES_SRC = "tests/fixtures"


def layout(repo_root: Path = REPO_ROOT) -> list[tuple[Path, str]]:
    """(source file, path relative to dist/tools/) for everything published."""
    pairs = [(repo_root / src, dst) for src, dst in TOOL_FILES]
    fixtures_dir = repo_root / FIXTURES_SRC
    for f in sorted(fixtures_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            pairs.append((f, f"fixtures/{f.name}"))
    return pairs


def publish(dist: Path, repo_root: Path = REPO_ROOT) -> list[Path]:
    tools = dist / "tools"
    written: list[Path] = []
    for src, rel in layout(repo_root):
        if not src.is_file():
            raise SystemExit(f"{src} missing — cannot publish the standalone tools")
        dst = tools / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(dst)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy the standalone tools into dist/tools/.")
    parser.add_argument("--dist", default=str(REPO_ROOT / "dist"), help="dist root (default: <repo>/dist)")
    args = parser.parse_args(argv)
    written = publish(Path(args.dist).resolve())
    for p in written:
        print(f"wrote {p}")
    print(f"{len(written)} tool file(s) published under {Path(args.dist).resolve() / 'tools'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
