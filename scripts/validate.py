#!/usr/bin/env python3
"""validate.py — the butler-skills CI validator.

Usage:
    scripts/validate.py <dir> [<dir> ...]        # registry mode: a checkout of a listed skill or template
    scripts/validate.py --all                    # clone and validate every templates.json AND skills.json entry
    scripts/validate.py --all --maintainer       # allow the butler- prefix / reserved-adjacent names
    scripts/validate.py <dir> --json             # machine-readable output
    scripts/validate.py --standalone <dir>       # any directory holding one skill or template (its own repo)

Standalone copy (no registry checkout needed — publish.yml puts this exact file on the
Pages site; an author runs it from their own repo):

    curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
    python3 validate.py --standalone .

This file is therefore a single-file tool: Python 3.11 stdlib only, no imports from the
other scripts, and the reserved-name list is embedded (schema/reserved-names.json is read
when it exists next to a registry checkout; tests assert the two agree).

The hub publishes two kinds, and a repository is exactly one of them — the kind is
read off the repo itself, never passed in:

  a duty template — `recipe.json`, `duty.py`, `README.md` at the root. A standing
  program: one Python program plus the manifest that names it, describes its
  settings and lists what triggers it accepts. (`recipe.json` present.)

  a skill — `SKILL.md`, `README.md`, `CHANGELOG.md` at the root. A playbook that
  teaches the butler a capability; the butler installs it on demand and Mastra
  lists it among the agent's skills. (`SKILL.md` present, no `recipe.json`.)

A repository holding both is refused. SKILL_STANDARD.md and TEMPLATE_STANDARD.md
write out every rule below.

Two modes:

  registry (default) — the directory is a checkout of an entry this registry
  lists, named after that entry in templates.json / skills.json, so the
  template's recipe.json `id` / the skill's frontmatter `name` must equal the
  directory name: that is the name it is published and installed under.
  `--all` clones every templates.json and skills.json entry at its ref into a
  temporary directory and validates those — and then holds the listed skills
  to each other: every skill a listed skill names in `requires.skills` must be
  listed too, and no requirements may form a cycle. The link itself — that
  `repo` is an https://github.com/<owner>/<repo> URL and that `ref` resolves —
  is scripts/check_registry.py's job, not this file's.

  --standalone — the directory is a skill or template repository checked out
  anywhere. The name comes from recipe.json / SKILL.md alone (it only has to be
  valid); every other rule is identical, except the one only a listing can
  answer — whether each skill in `requires.skills` is listed — which the
  registry PR and the publish build check.

Python 3.11 stdlib only. No network access except `--all`, which clones the
listed entries (nothing is checked out in this repo). Exits 1 on any failing
check and prints one field-by-field message per failure. This script is the
source of truth for what a passing PR looks like.
"""
from __future__ import annotations

import ast
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import deque
from contextlib import ExitStack
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "templates.json"
SCHEMA_PATH = REPO_ROOT / "schema" / "recipe.schema.json"
RESERVED_PATH = REPO_ROOT / "schema" / "reserved-names.json"

MAX_DESCRIPTION = 200  # composed into a 280-char field with a settings clause appended;
                       # a longer one is silently dropped by the caller.

# Tree rules, mirrored by the container's template installer (it refuses a
# checkout that breaks any of these before copying a single file).
MAX_TREE_FILES = 50
MAX_TREE_BYTES = 1024 * 1024
TREE_SKIP_NAMES = {".git", "__pycache__"}

REQUIRED_FILES = ("recipe.json", "duty.py", "README.md")

# Id prefixes. `butler-` is the Butler team's namespace for templates published through
# this hub (maintainer-only: --maintainer / MAINTAINER=1). `bevo-` is the container's
# own bundled-command namespace and is refused outright — a template with that prefix
# would collide with, or masquerade as, a bundled command.
MAINTAINER_PREFIX = "butler-"
CONTAINER_PREFIX = "bevo-"

# Mirror of schema/reserved-names.json (the source of truth in a registry checkout),
# embedded so the standalone copy of this file needs nothing beside it.
# tests/test_validate.py fails if the two drift.
RESERVED_NAMES_BUILTIN = frozenset({
    "bevo-onchain",
    "bevo-duty-creator",
    # Pre-rename name of bevo-duty-creator. Still reserved: the dir persists on
    # every console provisioned before the Duty rename.
    "bevo-automation-creator",
    "web-checkout",
    "bevo-skill-creator",
    "bevo-service-creator",
    # The two SKILL.md skills compiled into the butler image and written into
    # <workspace>/skills/ at boot. A hub skill under either name would collide with them.
    "acp-cli",
    "duty-code",
    "clawhub",
})

# Tooling a template author downloads next to recipe.json to validate locally;
# it must never be committed into the template — warn when seen.
TOOLING_FILES = ("validate.py", "replay.py", "stub_bevo.py")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SUPERSEDES_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}@\d+$")
TRIGGER_KINDS = {"timer", "group", "trade"}

# The JSON-Schema subset the container's params checker supports. Anything
# outside this keyword set is refused wherever it appears in `params` — a
# keyword the checker does not implement would silently do nothing.
PARAM_SCHEMA_KEYWORDS = {
    "type", "enum", "const", "minimum", "maximum", "minLength", "maxLength",
    "items", "default", "required", "additionalProperties", "description", "properties",
}
PARAM_SCHEMA_TYPES = {"object", "string", "number", "integer", "boolean", "array"}

SECRET_PATTERNS = [
    re.compile(r"\bbrt_[A-Za-z0-9]+"),
    re.compile(r"\bsk-[A-Za-z0-9]+"),
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
]

URL_RE = re.compile(r"https?://[^\s`)]+")
ALLOWED_URL_PREFIXES = ("https://github.com/Virtual-Protocol", "https://raw.githubusercontent.com/Virtual-Protocol")

# --- duty.py rules -----------------------------------------------------------------------

# The generators a duty's main loop iterates. Code with declared triggers that
# never calls one of these runs once and exits — a crash, not a duty.
# `batches` is a first-class waiter (the burst-shaped form of `events()`), so
# `for batch in bevo.batches():` alone must not be refused.
WAITERS = frozenset({
    "events", "trades", "messages", "transfers", "ticks", "polls", "webhooks", "frames", "batches",
})

# Everything a duty.py may import without shipping it: the SDK plus a small
# stdlib allowlist. A duty gets no site-packages of its own.
STDLIB_ALLOW = frozenset({
    "bevo", "json", "os", "re", "math", "datetime", "zoneinfo", "subprocess", "shlex",
    "time", "random", "collections", "itertools", "statistics",
})

FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_MODULES = {"socket", "urllib", "requests", "http"}

# Retired SDK names — a duty filed against one is a crash loop waiting to
# happen, so every refusal names the replacement. The nine money verbs were
# joined on 2026-09-21: there is no wrapper around `acp` any more, a duty runs
# the command directly (subprocess + a literal --idempotency-key).
_ACP_TRADE_REPLACEMENT = "run `acp trade` with subprocess and a literal --idempotency-key"
RETIRED_BEVO_CALLS = {
    "trade": _ACP_TRADE_REPLACEMENT,
    "execute": _ACP_TRADE_REPLACEMENT,
    "buy": _ACP_TRADE_REPLACEMENT,
    "sell": _ACP_TRADE_REPLACEMENT,
    "long": _ACP_TRADE_REPLACEMENT,
    "short": _ACP_TRADE_REPLACEMENT,
    "close": _ACP_TRADE_REPLACEMENT,
    "stock_buy": _ACP_TRADE_REPLACEMENT,
    "stock_sell": _ACP_TRADE_REPLACEMENT,
    "escalate": "bevo.prompt",
    "token": 'bevo.read("/token-price")',
    "is_stock": "no replacement — read spot.stocks[] via bevo.read(\"/user-assets\")",
}

# Money moves through a shelled `acp` command now: `subprocess.run(["acp", "trade", ...])`.
# Only these two-word acp command groups move the owner's money.
SHELL_CALL_ATTRS = {"run", "check_output", "Popen", "call", "check_call"}
MONEY_BIN = "acp"
MONEY_SUBCOMMANDS = {"trade", "wallet", "card"}
IDEMPOTENCY_FLAG = "--idempotency-key"

# Every os.environ key a duty may read besides its own declared params.
ENV_ALLOWLIST = frozenset({
    "PARAMS", "BEVO_SERVICE_ID", "BEVO_SERVICE_NAME", "BEVO_SESSION_ID",
    "BEVO_SESSION_KEY", "BEVO_MODE", "BEVO_STATE_PATH",
})


class Issues:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, field: str, msg: str) -> None:
        self.errors.append(f"{field}: {msg}")

    def warn(self, field: str, msg: str) -> None:
        self.warnings.append(f"{field}: {msg}")

    @property
    def ok(self) -> bool:
        return not self.errors


# --- layout --------------------------------------------------------------------------------


def iter_tree(template_dir: Path):
    """Yield (path, relative-posix-path) for every entry below template_dir,
    skipping TREE_SKIP_NAMES at the top level only. Symlinks are yielded (not
    followed) so check_layout can refuse them; nested .git entries are yielded
    so it can refuse nested repositories."""
    for root, dirs, files in os.walk(template_dir, followlinks=False):
        root_path = Path(root)
        top = root_path == template_dir
        keep: list[str] = []
        for d in dirs:
            p = root_path / d
            if top and d in TREE_SKIP_NAMES:
                continue
            if d == "__pycache__":
                continue
            if p.is_symlink():
                yield p, p.relative_to(template_dir).as_posix()
                continue
            if d == ".git":
                yield p, p.relative_to(template_dir).as_posix()
                continue
            keep.append(d)
        dirs[:] = keep
        for f in files:
            if top and f in TREE_SKIP_NAMES:
                continue
            p = root_path / f
            yield p, p.relative_to(template_dir).as_posix()


def check_layout(template_dir: Path, issues: Issues) -> None:
    """recipe.json, duty.py and README.md must all exist at the root; the
    tree must carry no symlinks or nested repos and stay within the size caps
    the container's own clone path enforces."""
    for name in REQUIRED_FILES:
        if not (template_dir / name).is_file():
            issues.error("layout", f"missing required file: {name}")

    count = 0
    total = 0
    for p, rel in iter_tree(template_dir):
        if p.is_symlink():
            issues.error("layout", f"symlink not allowed: {rel}")
            continue
        base = p.name
        if base == ".gitmodules":
            issues.error("layout", f"nested submodules not allowed: {rel}")
            continue
        if base == ".git":
            issues.error("layout", f"nested git repository not allowed: {rel}")
            continue
        if p.is_file():
            count += 1
            total += p.stat().st_size
    if count > MAX_TREE_FILES:
        issues.error("layout", f"{count} files, must be <= {MAX_TREE_FILES}")
    if total > MAX_TREE_BYTES:
        issues.error("layout", f"{total} bytes in total, must be <= {MAX_TREE_BYTES}")
    for tool in TOOLING_FILES:
        if (template_dir / tool).is_file():
            issues.warn("layout", f"{tool} looks like downloaded hub tooling — keep it out of the commit")


# --- recipe.json -----------------------------------------------------------------------


def check_params_schema(node, path: str, issues: Issues) -> None:
    """Recurse into a `params` JSON-Schema fragment, refusing any keyword the
    container's params checker does not implement — a keyword outside this
    set would silently do nothing there, so a template author must never
    believe it works."""
    if not isinstance(node, dict):
        issues.error("params", f"{path}: must be an object, got {type(node).__name__}")
        return
    extra = set(node.keys()) - PARAM_SCHEMA_KEYWORDS
    if extra:
        issues.error("params", f"{path}: unsupported JSON-Schema keyword(s) {sorted(extra)}")
    t = node.get("type")
    if t is not None and t not in PARAM_SCHEMA_TYPES:
        issues.error("params", f"{path}.type: {t!r} is not one of {sorted(PARAM_SCHEMA_TYPES)}")
    props = node.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            issues.error("params", f"{path}.properties: must be an object")
        else:
            for name, sub in props.items():
                check_params_schema(sub, f"{path}.properties.{name}", issues)
    items = node.get("items")
    if items is not None:
        if isinstance(items, list):
            for i, sub in enumerate(items):
                check_params_schema(sub, f"{path}.items[{i}]", issues)
        else:
            check_params_schema(items, f"{path}.items", issues)
    required = node.get("required")
    if required is not None and not isinstance(required, list):
        issues.error("params", f"{path}.required: must be an array")


def check_params_conditionals(params: dict, issues: Issues) -> None:
    """Validate `params.allOf` — the ONE conditional shape the container's params
    checker implements (`conditionalClauses` in
    virtuals-agent/src/integrations/butler/bin/automation/recipe-params.mjs): a
    list of `{if: {properties: {<name>: {const: ...} | {enum: [...]}}}, then:
    {required: [...]}}` clauses at the ROOT of `params` only. `allOf` anywhere
    else in the tree stays refused by check_params_schema's keyword allowlist —
    this function is only ever called on the root node, never recursively.

    Semantics enforced here match the container exactly: a condition holds only
    when the named property is present on the filed params, and `then`
    contributes `required` alone (no other JSON-Schema effect). Anything wider
    (nested allOf, else, other if/then keywords, non-const/enum conditions) is
    refused so a template author can never believe a broader shape works.
    """
    all_of = params.get("allOf")
    if not isinstance(all_of, list) or not all_of:
        issues.error("params", "params.allOf: must be a non-empty array")
        return

    declared_props = params.get("properties")
    declared_props = declared_props if isinstance(declared_props, dict) else {}

    for i, clause in enumerate(all_of):
        prefix = f"params.allOf[{i}]"
        if not isinstance(clause, dict):
            issues.error("params", f"{prefix}: must be an object, got {type(clause).__name__}")
            continue
        extra = set(clause.keys()) - {"if", "then"}
        if extra:
            issues.error("params", f"{prefix}: unsupported key(s) {sorted(extra)} — only 'if' and 'then'")

        cond = clause.get("if")
        if not isinstance(cond, dict) or set(cond.keys()) != {"properties"}:
            issues.error("params", f"{prefix}.if: must be an object with exactly the key 'properties'")
        else:
            cond_props = cond.get("properties")
            if not isinstance(cond_props, dict) or not cond_props:
                issues.error("params", f"{prefix}.if.properties: must be a non-empty object")
            else:
                for name, sub in cond_props.items():
                    cprefix = f"{prefix}.if.properties.{name}"
                    if name not in declared_props:
                        issues.error("params", f"{cprefix}: {name!r} is not declared in params.properties")
                        continue
                    if not isinstance(sub, dict) or len(sub) != 1 or next(iter(sub)) not in ("const", "enum"):
                        issues.error(
                            "params",
                            f"{cprefix}: must be an object with exactly one key, 'const' or 'enum'",
                        )
                        continue
                    key = next(iter(sub))
                    value = sub[key]
                    if key == "enum" and (not isinstance(value, list) or not value):
                        issues.error("params", f"{cprefix}.enum: must be a non-empty array")
                        continue
                    prop_enum = declared_props[name].get("enum") if isinstance(declared_props[name], dict) else None
                    if isinstance(prop_enum, list) and prop_enum:
                        candidates = value if key == "enum" else [value]
                        bad = [v for v in candidates if v not in prop_enum]
                        if bad:
                            issues.error(
                                "params",
                                f"{cprefix}.{key}: value(s) {bad!r} not in {name!r}'s own enum {prop_enum!r}",
                            )

        then = clause.get("then")
        if not isinstance(then, dict) or set(then.keys()) != {"required"}:
            issues.error("params", f"{prefix}.then: must be an object with exactly the key 'required'")
        else:
            required = then.get("required")
            if not isinstance(required, list) or not required or not all(isinstance(r, str) for r in required):
                issues.error("params", f"{prefix}.then.required: must be a non-empty array of strings")
            else:
                for name in required:
                    if name not in declared_props:
                        issues.error(
                            "params", f"{prefix}.then.required: {name!r} is not declared in params.properties"
                        )


def check_recipe_json(recipe: dict, expected_id: str | None, issues: Issues) -> None:
    """expected_id is the directory/registry name in registry mode, or None
    in --standalone mode (the id only has to be a valid template id there)."""
    for req in ("id", "version", "description", "params"):
        if req not in recipe:
            issues.error(req, "required field missing")

    rid = recipe.get("id")
    if rid is not None:
        if not isinstance(rid, str) or not ID_RE.match(rid):
            issues.error("id", f"must match ^[a-z0-9][a-z0-9-]{{1,63}}$, got {rid!r}")
        elif expected_id is not None and rid != expected_id:
            issues.error("id", f"recipe.json id {rid!r} must equal the registry name {expected_id!r}")

    version = recipe.get("version")
    if version is not None and (not isinstance(version, int) or isinstance(version, bool) or version < 1):
        issues.error("version", f"must be a positive integer, got {version!r}")

    description = recipe.get("description")
    if description is not None:
        if not isinstance(description, str) or not description.strip():
            issues.error("description", "must be a non-empty string")
        elif len(description) > MAX_DESCRIPTION:
            issues.error("description", f"must be <= {MAX_DESCRIPTION} chars, got {len(description)}")

    keywords = recipe.get("keywords")
    if keywords is not None and (not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords)):
        issues.error("keywords", "must be an array of strings")

    triggers = recipe.get("triggers")
    if triggers is not None:
        if not isinstance(triggers, list):
            issues.error("triggers", "must be an array")
        else:
            unknown = set(triggers) - TRIGGER_KINDS
            if unknown:
                issues.error("triggers", f"unknown trigger kind(s) {sorted(unknown)}; only {sorted(TRIGGER_KINDS)}")

    supersedes = recipe.get("supersedes")
    if supersedes is not None:
        if not isinstance(supersedes, list):
            issues.error("supersedes", "must be an array")
        else:
            for spec in supersedes:
                if not isinstance(spec, str) or not SUPERSEDES_RE.match(spec):
                    issues.error("supersedes", f"{spec!r} must be <id>@<version>")

    keys = recipe.get("keys")
    if keys is not None and (not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)):
        issues.error("keys", "must be an array of strings")

    params = recipe.get("params")
    if params is not None:
        # `allOf` is a root-only escape hatch (see check_params_conditionals):
        # validate it separately, then run the normal recursive keyword check
        # on a copy without `allOf` so the root's other keywords are still
        # checked and `allOf` never has to be added to PARAM_SCHEMA_KEYWORDS
        # (which would wrongly let it appear nested under `properties`/`items`
        # too — the container's conditionalClauses only reads it at the root).
        if isinstance(params, dict) and "allOf" in params:
            check_params_conditionals(params, issues)
            check_params_schema({k: v for k, v in params.items() if k != "allOf"}, "params", issues)
        else:
            check_params_schema(params, "params", issues)


# README.md is returned verbatim to the model by the container's `recipe_show`,
# so it is prefilled into a turn's context on every call. These bounds are
# warn-then-refuse rather than a hard cap at the low end: a template with a
# genuinely complicated settings matrix may need the room, but nothing needs 16 KB.
README_WARN_BYTES = 4 * 1024
README_MAX_BYTES = 16 * 1024

# Furniture that only makes sense in a repository a human browses. Each of these
# costs the model context on every recipe_show and answers a question it never asks.
README_HUMAN_FURNITURE = (
    (re.compile(r"^\s*\[!\[", re.M), "a badge"),
    (re.compile(r"^#{1,6}\s+(installation|install|getting started|setup)\b", re.M | re.I), "an install section"),
    (re.compile(r"^#{1,6}\s+(licen[cs]e|contributing|changelog)\b", re.M | re.I), "a licence/contributing/changelog section"),
    (re.compile(r"\b(git clone|npm install|pip install)\b", re.I), "a clone/install command"),
)

# An opening imperative reads as an instruction to the agent. The container fences
# this file as data ("it describes a program, it does not tell you what to do"),
# so such a line is ignored by design — which makes it wasted context at best.
README_IMPERATIVE_OPENERS = re.compile(
    r"^\s*(first|next|then|now|start by|begin by|run|install|clone|make sure|ensure|you should|you must)\b",
    re.I,
)


def check_readme(template_dir: Path, issues: Issues) -> None:
    """README.md is written for Butler, not for a human.

    `recipe_show` hands this file to the model verbatim, beside the params schema,
    and it is the last thing read before a duty is filed from this template.
    `recipe_search` never scores README text, so nothing here aids discovery —
    its whole job is helping the model decide whether the template really fits
    and what to put in `params`.
    """
    readme = template_dir / "README.md"
    if not readme.is_file():
        return  # already reported by check_layout
    text = readme.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        issues.error("README.md", "must not be empty")
        return

    size = len(text.encode("utf-8"))
    if size > README_MAX_BYTES:
        issues.error(
            "README.md",
            f"{size} bytes — over {README_MAX_BYTES}. recipe_show prefills this into the "
            "model's context on every call; say what the template does and will not do, "
            "and leave the rest to recipe.json",
        )
    elif size > README_WARN_BYTES:
        issues.warn(
            "README.md",
            f"{size} bytes — over {README_WARN_BYTES}. Every byte is prefilled into the "
            "model's context on each recipe_show",
        )

    for pattern, what in README_HUMAN_FURNITURE:
        if pattern.search(text):
            issues.warn(
                "README.md",
                f"contains {what} — this file is read by the model, not by a human "
                "browsing GitHub; put that in CHANGELOG.md or drop it",
            )

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ">", "|", "`")):
            continue
        if README_IMPERATIVE_OPENERS.match(stripped):
            issues.warn(
                "README.md",
                f"opens a line with an instruction ({stripped[:40]!r}) — the container "
                "hands this file to the model as DATA, not instructions, so describe "
                "what the program does rather than telling the reader what to do",
            )
        break


def check_reserved(rid: str, reserved: set[str], maintainer: bool, issues: Issues) -> None:
    if not rid:
        return
    if rid in reserved:
        issues.error("id", f"{rid!r} is reserved (schema/reserved-names.json)")
    if rid.startswith(CONTAINER_PREFIX):
        issues.error(
            "id",
            f"{rid!r} uses the '{CONTAINER_PREFIX}' prefix, which is the container's bundled-command "
            f"namespace — templates may never use it; team templates use '{MAINTAINER_PREFIX}'",
        )
    elif rid.startswith(MAINTAINER_PREFIX) and not maintainer:
        issues.error(
            "id",
            f"{rid!r} uses the maintainer-only '{MAINTAINER_PREFIX}' prefix; pass --maintainer or set MAINTAINER=1 to publish it",
        )


def check_secrets_and_urls(full_text: str, issues: Issues) -> None:
    for pat in SECRET_PATTERNS:
        m = pat.search(full_text)
        if m:
            issues.error("secrets-lint", f"looks like a credential: {m.group(0)[:12]}...")
    for m in URL_RE.finditer(full_text):
        url = m.group(0)
        if not any(url.startswith(p) for p in ALLOWED_URL_PREFIXES):
            issues.error("url-lint", f"disallowed URL {url!r} (only github.com/Virtual-Protocol links allowed)")
    if "​" in full_text:
        issues.error(
            "invisible-char-lint",
            "file contains U+200B (zero-width space) — this can hide code-fence-breaking "
            "tricks and paste invisibly into a copy; remove it",
        )


# --- duty.py ---------------------------------------------------------------------------


def _attr_call_name(node: ast.AST) -> tuple[str | None, str | None]:
    """(base, attr) for a `base.attr(...)` Call's func, else (None, None)."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None, None


def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _contains_waiter_call(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            base, attr = _attr_call_name(sub.func)
            if base == "bevo" and attr in WAITERS:
                return True
    return False


def check_duty_py(template_dir: Path, recipe: dict, issues: Issues) -> None:
    duty_path = template_dir / "duty.py"
    if not duty_path.is_file():
        return  # already reported by check_layout

    src = duty_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(duty_path))
    except SyntaxError as e:
        issues.error("duty.py", f"ast parse failed: {e}")
        return

    has_import_bevo = False
    uses_bevo_attr = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if alias.name == "bevo":
                    has_import_bevo = True
                    if alias.asname:
                        issues.error("duty.py", "forbidden: aliasing 'import bevo as ...'")
                elif base not in STDLIB_ALLOW:
                    issues.error("duty.py", f"line {node.lineno}: forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "bevo":
                issues.error("duty.py", f"line {node.lineno}: forbidden: 'from bevo import ...' (use 'import bevo')")
            elif node.module and node.module.split(".")[0] not in STDLIB_ALLOW:
                issues.error("duty.py", f"line {node.lineno}: forbidden import: {node.module}")

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "bevo":
            uses_bevo_attr = True

        if isinstance(node, ast.Call):
            fname = None
            base = attr = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                base, attr = _attr_call_name(node.func)
                fname = attr
                if base == "os" and attr in ("system", "popen"):
                    issues.error("duty.py", f"line {node.lineno}: forbidden call: os.{attr}(...)")
            # Only a BARE call is the builtin. `re.compile(...)` is an attribute
            # call on an allowed module and is used by every shipped template to
            # precompile an address pattern; reading it as the builtin `compile`
            # refused the two templates this registry exists to serve.
            if isinstance(node.func, ast.Name) and fname in FORBIDDEN_CALLS:
                issues.error("duty.py", f"line {node.lineno}: forbidden call: {fname}(...)")
            if base in FORBIDDEN_MODULES:
                issues.error("duty.py", f"line {node.lineno}: forbidden call into {base}.{attr}(...)")

            if base == "bevo" and attr in RETIRED_BEVO_CALLS:
                issues.error(
                    "duty.py",
                    f"line {node.lineno}: bevo.{attr}(...) is retired — {RETIRED_BEVO_CALLS[attr]}",
                )

            if base == "subprocess" and attr in SHELL_CALL_ATTRS:
                _check_shell_money_call(node, issues)

        if isinstance(node, ast.While):
            test_is_true = isinstance(node.test, ast.Constant) and node.test.value is True
            if test_is_true and not _contains_waiter_call(node):
                issues.error(
                    "duty.py",
                    f"line {node.lineno}: 'while True:' with no waiter call inside it "
                    f"(bevo.{'/'.join(sorted(WAITERS))}) never yields to the supervisor",
                )

    if uses_bevo_attr and not has_import_bevo:
        issues.error("duty.py", "uses bevo.* but never 'import bevo'")

    triggers = recipe.get("triggers") or []
    if triggers and not _contains_waiter_call(tree):
        issues.error(
            "duty.py",
            f"recipe.json declares triggers {triggers!r} but duty.py never calls a waiter "
            f"(bevo.{'/'.join(sorted(WAITERS))})",
        )

    # os.environ keys read, other than what recipe.json's params declare or the
    # runtime always provides.
    declared = set((recipe.get("params") or {}).get("properties") or {}) | ENV_ALLOWLIST
    env_keys: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = _const_str(node.slice)
            if key:
                env_keys.add((key, node.lineno))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and _is_os_environ(node.func.value) and node.args:
                key = _const_str(node.args[0])
                if key:
                    env_keys.add((key, node.lineno))
    undeclared = sorted({k for k, _ in env_keys if k not in declared})
    if undeclared:
        issues.error("duty.py", f"os.environ keys not declared in params.properties: {undeclared}")


def _check_shell_money_call(node: ast.Call, issues: Issues) -> None:
    """`subprocess.run(["acp", "trade", ...])`-shaped calls are how a duty now
    spends. Refuse one that omits a literal --idempotency-key; warn (never
    refuse) when the argv is not a literal list, since the key cannot be read
    statically in that shape."""
    if not node.args:
        return
    first = node.args[0]
    if not isinstance(first, (ast.List, ast.Tuple)):
        issues.warn(
            "duty.py",
            f"line {node.lineno}: subprocess argv is not a literal list — cannot verify "
            f"statically whether this is a money command with an {IDEMPOTENCY_FLAG}",
        )
        return

    elts = first.elts
    literal = [_const_str(e) for e in elts]
    has_dynamic = any(e is None for e in literal) or any(isinstance(x, ast.Starred) for x in elts)

    if len(literal) < 2 or literal[0] != MONEY_BIN or literal[1] not in MONEY_SUBCOMMANDS:
        return  # not a recognized acp money command

    literal_strs = [s for s in literal if s is not None]
    if IDEMPOTENCY_FLAG in literal_strs:
        return
    if has_dynamic:
        issues.warn(
            "duty.py",
            f"line {node.lineno}: acp {literal[1]} argv is partly dynamic — cannot verify "
            f"statically that it carries {IDEMPOTENCY_FLAG}",
        )
    else:
        issues.error(
            "duty.py",
            f"line {node.lineno}: acp {literal[1]} shelled with no literal {IDEMPOTENCY_FLAG}",
        )


# --- skills: SKILL.md --------------------------------------------------------------------
#
# The hub's other kind. A skill is a SKILL.md playbook that teaches the butler a
# capability for a one-off turn: the butler installs it on demand into
# <workspace>/skills/<name>/, Mastra lists it among the agent's skills, and the model
# reads it when a request fits. Only SKILL.md and references/**/*.md are published —
# README.md and CHANGELOG.md are for the humans maintaining the repo.
#
# Mastra reads SKILL.md with gray-matter (js-yaml underneath) and a skill whose
# frontmatter fails to parse or validate is DROPPED from discovery without a word to
# anyone — it looks installed and never appears. So the frontmatter rules below are
# deliberately narrower than YAML: anything they accept, YAML reads back unchanged.

SKILLS_REGISTRY_PATH = REPO_ROOT / "skills.json"

SKILL_REQUIRED_FILES = ("SKILL.md", "README.md", "CHANGELOG.md")

# Mastra's own rule (validateSkillName in @mastra/core's workspace skills): lowercase
# letters, digits and single hyphens, never leading or trailing, at most 64 characters —
# and the name must equal the directory the skill is installed in.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_SKILL_NAME = 64
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_SKILL_DESCRIPTION = 200
MAX_SKILL_BODY_CHARS = 12000

# metadata.butler.maxSteps (optional): how many agent steps a turn that loads the skill
# may take. A butler turn gets 20 by default and the container caps whatever a skill
# asks for at its own ceiling (200 unless configured otherwise); a phone errand needs ~150.
MIN_SKILL_MAX_STEPS = 20
MAX_SKILL_MAX_STEPS = 500
# metadata.butler.requires.skills (optional): the skills this one builds on. The butler's
# hub installs them first, so each must be listed in skills.json as well — checked over
# the whole listing by --all and by the publish build, never by --standalone.
MAX_REQUIRED_SKILLS = 5

SKILL_FRONTMATTER_KEYS = ("name", "description", "version", "metadata")
SKILL_FRONTMATTER_LINE_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:[ \t]+(.*?))?[ \t]*$")

# A plain (unquoted) YAML scalar may not start with an indicator character.
YAML_INDICATORS = frozenset("-?:,[]{}#&*!|>'\"%@`")
# Characters js-yaml refuses anywhere in its input ("the stream contains non-printable
# characters"), plus the Unicode line separators Python would split a line on and YAML
# would not.
YAML_UNSAFE_CHAR_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffe\uffff\u2028\u2029]")

# Plain scalars js-yaml resolves to something other than a string: null, booleans,
# ints (decimal, octal, 0b/0o/0x, sexagesimal), floats, and dates. Mastra then sees
# a non-string `name`/`description` and drops the skill.
_YAML_NON_STRING_RE = re.compile(
    r"^(?:~|null|Null|NULL|true|True|TRUE|false|False|FALSE"
    r"|[-+]?(?:0b[01_]+|0o[0-7_]+|0x[0-9a-fA-F_]+|[0-9][0-9_]*(?::[0-5]?[0-9])*)"
    r"|[-+]?(?:0|[1-9][0-9_]*)(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?"
    r"|\.[0-9_]+(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:(?:[Tt]|[ \t]+)[0-9].*)?"
    r")$"
)

# The seven sections of a skill body, in this order. Anything else is a `###`
# subsection of one of them.
SKILL_SECTIONS = (
    "When to use",
    "Before you start",
    "Procedure",
    "Idempotency and retries",
    "Failure handling",
    "Limits",
    "Say to the owner",
)
PROCEDURE_SECTION = "Procedure"
IDEMPOTENCY_SECTION = "Idempotency and retries"
# Sections of the retired OpenClaw-era grammar, with where their content goes now.
RETIRED_SECTIONS = {
    "customize": "retired with metadata.butler.params — say what to ask the owner under `## Before you start`",
    "one-off procedure": "renamed — the numbered steps live under `## Procedure`",
    "duty procedure": "retired — a standing order is a duty template (templates.json), never a skill section",
    "contracts": "retired with metadata.butler.web3",
}

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)")
STEP_RE = re.compile(r"^\s*\d+[.)][ \t]")
STEP_MARKER_RE = re.compile(r"\[(FIXED|ADAPT)\]")
SHELL_LANGS = frozenset({"", "sh", "bash", "shell", "console", "zsh", "shell-session"})

# The commands a skill may run, and nothing else. The bevo-* names are virtuals-agent's
# BUTLER_COMMANDS (src/integrations/butler/launchers.ts); `acp` is the generated wrapper
# around the pinned acp-cli (src/integrations/acp/wrapper.ts); `app-checkout` is the phone
# rail's container primitive.
SKILL_COMMANDS = frozenset({
    "bevo-read", "bevo-send", "bevo-rpc", "bevo-notify", "bevo-sms", "bevo-x", "bevo-automation",
    "acp", "app-checkout",
})

# Subcommand tables, each read off the command's own dispatch in virtuals-agent
# (src/integrations/butler/bin/<command>.mjs). The subcommand is argv[0] for all of them.
SKILL_SUBCOMMANDS = {
    "bevo-read": frozenset({  # read.mjs COMMANDS
        "get", "messages", "channel-messages", "participants", "summary", "search", "groups",
        "user", "assets", "me", "policy", "token-search", "token", "token-balance", "token-price",
        "stock-min", "token-stats", "trade-activity", "trade-executions", "wallet-transfers",
        "request", "card-budget",
    }),
    "bevo-sms": frozenset({"number", "status", "otp"}),  # sms.mjs COMMANDS
    "bevo-automation": frozenset({  # automation.mjs main()
        "create", "validate", "update", "enable", "disable", "delete", "list", "show", "logs",
    }),
    "bevo-x": frozenset({"search"}),  # x.mjs
    "app-checkout": frozenset({
        "start", "status", "screen", "shot", "tap", "type", "key", "swipe", "wait", "install",
        "open", "checkpoint", "end",
    }),
}

# The acp command groups the container's wrapper lets through (wrapper.ts): the groups
# acp-cli runs on its access token alone, plus trade/wallet/card, which the butler rail
# forwards to bevo-server's signer. `client`, `compute` and `configure` are refused there.
# The group is the first non-option argument, as the wrapper reads it.
ACP_GROUPS = frozenset({
    "agent", "browse", "card", "chain", "email", "events", "job", "message", "offering",
    "policy", "provider", "resource", "skill", "subscription", "trade", "wallet",
})
ACP_REFUSED_GROUPS = frozenset({"client", "compute", "configure"})
ACP_AGENT_SUBCOMMANDS = frozenset({  # wrapper.ts AGENT_ALLOWED
    "whoami", "list", "use", "link", "generate-signer-key", "signer-status", "help",
})

# Raw network or interpreter access: a skill reads through bevo-read / bevo-rpc and
# runs no code of its own.
FORBIDDEN_SKILL_COMMAND_RE = re.compile(r"^(?:curl|wget|node|nodejs|python(?:\d+(?:\.\d+)*)?)$")

SHELL_SEPARATORS = frozenset({"|", "||", "&&", ";", "&", "|&", ";;", ";&", ";;&"})
HEREDOC_RE = re.compile(r"(?<!<)<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Invisible characters that can hide a code-fence break or reorder what a reviewer sees
# (zero-width, word joiner, BOM, and the bidirectional overrides).
INVISIBLE_CHARS = {
    "\u200b": "U+200B zero-width space",
    "\u200c": "U+200C zero-width non-joiner",
    "\u200d": "U+200D zero-width joiner",
    "\u2060": "U+2060 word joiner",
    "\ufeff": "U+FEFF zero-width no-break space",
    "\u202a": "U+202A bidi override", "\u202b": "U+202B bidi override",
    "\u202c": "U+202C bidi override", "\u202d": "U+202D bidi override",
    "\u202e": "U+202E bidi override", "\u2066": "U+2066 bidi isolate",
    "\u2067": "U+2067 bidi isolate", "\u2068": "U+2068 bidi isolate",
    "\u2069": "U+2069 bidi isolate",
    "\u2028": "U+2028 line separator", "\u2029": "U+2029 paragraph separator",
}

OVERRIDE_PHRASES = (
    "ignore previous", "ignore all previous", "override", "SOUL.md", "do not tell",
    "disregard your instructions",
)
RAW_ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME)\b")

# The retired OpenClaw runtime (bevo-docker), and what a skill says instead.
RETIRED_RUNTIME_TERMS = {
    "bevo-hub": "the OpenClaw-era skill manager — the Mastra butler installs hub skills itself",
    "agents.md": "the OpenClaw-era system prompt — say the rule in this skill instead of citing a section",
    "openclaw": "the retired runtime — this skill runs on the Mastra butler (virtuals-agent)",
    "bevo-location": "a retired command — location is the butler's request_location tool",
    "bevo-duty": "a retired name — the duty CLI is bevo-automation",
    "web-checkout": "a retired command the Mastra butler does not have",
}
RETIRED_RUNTIME = "the retired OpenClaw runtime (bevo-docker)"
RETIRED_METADATA_KEYS = {
    "openclaw": "its requires.bins moves to metadata.butler.requires.bins",
}
RETIRED_BUTLER_KEYS = {
    "tier": "every hub skill is installed on demand",
    "modes": "a skill is guidance for a turn; a standing order is a duty template",
    "params": "say what to ask the owner under `## Before you start`",
    "web3": "the contract constants it carried are not published",
    "dutyTemplate": "a standing program is a duty template in templates.json",
}
RETIRED_REQUIRES_KEYS = {
    "routes": "each command owns its routes",
    "features": "each command owns its routes",
    "gates": "the server applies the owner's gates",
}

# Only SKILL.md and these are published; the path shape keeps them plain URL paths.
SKILL_REFERENCE_RE = re.compile(
    r"^references/(?:[A-Za-z0-9_-][A-Za-z0-9._-]*/)*[A-Za-z0-9_-][A-Za-z0-9._-]*\.md$"
)


def _yaml_plain_problem(value: str) -> str | None:
    """Why a YAML plain scalar would not read back as exactly `value`, or None."""
    if value[0] in YAML_INDICATORS:
        return f"starts with {value[0]!r}, a YAML indicator character"
    if re.search(r":(?:\s|$)", value):
        return "contains ': ' (or ends in ':'), which YAML reads as a mapping"
    if re.search(r"\s#", value):
        return "contains ' #', which YAML reads as the start of a comment"
    if _YAML_NON_STRING_RE.match(value):
        return "reads as a YAML number, boolean, null or date, not a string"
    return None


class _DuplicateKey(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _refuse_duplicate_keys(pairs):
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen[key] = value
    return seen


def _parse_skill_description(raw: str, issues: Issues) -> str | None:
    """The description as Mastra will read it: a JSON double-quoted string (which is
    also a valid YAML double-quoted scalar), or a plain scalar YAML reads verbatim."""
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as e:
            issues.error(
                "description",
                f"starts with '\"' but is not one JSON string ({e.msg}) — quote the whole value "
                "as a JSON string and put nothing after it",
            )
            return None
        if not isinstance(value, str):
            issues.error("description", "must be a string")
            return None
        return value
    problem = _yaml_plain_problem(raw)
    if problem:
        issues.error(
            "description",
            f"{problem} — gray-matter cannot read this frontmatter and Mastra silently drops "
            f"the skill. Quote it as a JSON string: description: {json.dumps(raw, ensure_ascii=False)}",
        )
    return raw


def _parse_skill_metadata(raw: str, issues: Issues):
    if not raw.startswith("{"):
        issues.error("metadata", "must be ONE line of JSON — an object on the same line as `metadata:`")
        return None
    try:
        return json.loads(raw, object_pairs_hook=_refuse_duplicate_keys)
    except _DuplicateKey as e:
        issues.error("metadata", f"duplicated key {e.key!r} — YAML refuses a duplicated key, and Mastra drops the skill")
    except json.JSONDecodeError as e:
        issues.error("metadata", f"must be one line of valid JSON: {e}")
    return None


def parse_skill_md(text: str, issues: Issues) -> dict | None:
    """SKILL.md's frontmatter and body, read the way Mastra reads them.

    Returns {"name", "description", "version", "metadata", "_keys", "_body",
    "_body_line", "_metadata_line"}, or None when the `---` fences are broken.
    Frontmatter is `key: value` lines only, each key once; `metadata` is one line of
    JSON; unparsed values stay None and the reason is in `issues`."""
    if text.startswith("\ufeff"):
        issues.error("frontmatter", "SKILL.md starts with a byte-order mark — save it as plain UTF-8")
        text = text[1:]
    # "\n" only, as gray-matter splits: str.splitlines() also breaks on U+2028 and
    # friends, which would let this reader see two lines where YAML sees one.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    if not lines or lines[0].rstrip() != "---":
        issues.error("frontmatter", "SKILL.md must start with a '---' line")
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        issues.error("frontmatter", "no closing '---' line")
        return None

    fm: dict = {key: None for key in SKILL_FRONTMATTER_KEYS}
    keys: dict[str, int] = {}
    for i in range(1, end):
        line = lines[i]
        if not line.strip():
            continue
        bad = YAML_UNSAFE_CHAR_RE.search(line)
        if bad:
            issues.error(
                "frontmatter",
                f"line {i + 1} holds U+{ord(bad.group(0)):04X}, a control or line-separator character "
                "js-yaml refuses — Mastra would drop the skill",
            )
            continue
        m = SKILL_FRONTMATTER_LINE_RE.match(line)
        if not m:
            issues.error(
                "frontmatter",
                f"line {i + 1} is not a one-line `key: value` pair: {line[:60]!r} "
                "(no YAML blocks, continuation lines or comments)",
            )
            continue
        key, raw = m.group(1), (m.group(2) or "")
        if key in keys:
            issues.error("frontmatter", f"line {i + 1}: {key!r} given twice — YAML refuses a duplicated key")
            continue
        keys[key] = i + 1
        if key not in SKILL_FRONTMATTER_KEYS:
            issues.error(
                "frontmatter",
                f"line {i + 1}: unknown key {key!r} — a skill's frontmatter is exactly "
                f"{', '.join(SKILL_FRONTMATTER_KEYS)}",
            )
            continue
        if not raw:
            issues.error(key, "must be on the same line as its key, and not empty")
            continue
        if key == "description":
            fm["description"] = _parse_skill_description(raw, issues)
        elif key == "metadata":
            fm["metadata"] = _parse_skill_metadata(raw, issues)
        else:
            fm[key] = raw

    fm["_keys"] = keys
    fm["_body"] = "\n".join(lines[end + 1:])
    fm["_body_line"] = end + 2
    fm["_metadata_line"] = keys.get("metadata")
    return fm


def check_skill_metadata(metadata, issues: Issues, name: str | None = None) -> dict | None:
    """`metadata` is {"butler": {"moneyMoving", "keywords", "requires": {"bins"}}}, plus
    the optional `maxSteps` and `requires.skills`, and nothing else. `name` is the
    skill's own name (a skill never requires itself). Returns the butler block when
    the fields the other checks read — moneyMoving, keywords, requires.bins — have
    the right shape."""
    if not isinstance(metadata, dict):
        issues.error("metadata", "must be a JSON object")
        return None
    for key in metadata:
        if key == "butler":
            continue
        if key in RETIRED_METADATA_KEYS:
            issues.error(
                f"metadata.{key}",
                f"belongs to {RETIRED_RUNTIME}; the Mastra butler never reads it — remove it "
                f"({RETIRED_METADATA_KEYS[key]})",
            )
        else:
            issues.error(f"metadata.{key}", 'unknown key — metadata holds only the "butler" block')
    butler = metadata.get("butler")
    if butler is None:
        issues.error("metadata.butler", "required block missing")
        return None
    if not isinstance(butler, dict):
        issues.error("metadata.butler", "must be an object")
        return None

    for key in butler:
        if key in ("moneyMoving", "keywords", "maxSteps", "requires"):
            continue
        if key in RETIRED_BUTLER_KEYS:
            issues.error(
                f"metadata.butler.{key}",
                f"a field of {RETIRED_RUNTIME} the Mastra butler never reads — remove it "
                f"({RETIRED_BUTLER_KEYS[key]})",
            )
        else:
            issues.error(f"metadata.butler.{key}", "unknown key — the block is moneyMoving, keywords, maxSteps, requires")

    ok = True
    if "moneyMoving" not in butler:
        issues.error("metadata.butler.moneyMoving", "required field missing")
        ok = False
    elif not isinstance(butler["moneyMoving"], bool):
        issues.error("metadata.butler.moneyMoving", "must be true or false")
        ok = False

    keywords = butler.get("keywords")
    if keywords is None:
        issues.error("metadata.butler.keywords", "required field missing")
        ok = False
    elif not isinstance(keywords, list) or not all(isinstance(k, str) and k.strip() for k in keywords):
        issues.error("metadata.butler.keywords", "must be an array of non-empty strings")
        ok = False

    if "maxSteps" in butler:
        steps = butler["maxSteps"]
        # bool is an int to Python; JSON true is not a step count.
        if isinstance(steps, bool) or not isinstance(steps, int) or not MIN_SKILL_MAX_STEPS <= steps <= MAX_SKILL_MAX_STEPS:
            issues.error(
                "metadata.butler.maxSteps",
                f"must be an integer from {MIN_SKILL_MAX_STEPS} to {MAX_SKILL_MAX_STEPS} — the agent steps a turn "
                f"that loads this skill may take (leave it out for the butler's default), got {json.dumps(steps)}",
            )

    requires = butler.get("requires")
    if requires is None:
        issues.error("metadata.butler.requires", 'required field missing — {"bins": [...]}')
        return None
    if not isinstance(requires, dict):
        issues.error("metadata.butler.requires", 'must be an object: {"bins": [...]}')
        return None
    for key in requires:
        if key in ("bins", "skills"):
            continue
        if key in RETIRED_REQUIRES_KEYS:
            issues.error(
                f"metadata.butler.requires.{key}",
                f"a field of {RETIRED_RUNTIME} the Mastra butler never reads — remove it "
                f"({RETIRED_REQUIRES_KEYS[key]})",
            )
        else:
            issues.error(f"metadata.butler.requires.{key}", "unknown key — requires holds only bins and skills")
    if "skills" in requires:
        check_required_skills(requires["skills"], name, issues)
    bins = requires.get("bins")
    if bins is None:
        issues.error("metadata.butler.requires.bins", "required field missing (an empty list when the skill runs no command)")
        return None
    if not isinstance(bins, list) or not all(isinstance(b, str) for b in bins):
        issues.error("metadata.butler.requires.bins", "must be an array of command names")
        return None
    for b in bins:
        if b not in SKILL_COMMANDS:
            issues.error(
                "metadata.butler.requires.bins",
                f"{b!r} is not a command a skill may run (one of {', '.join(sorted(SKILL_COMMANDS))})",
            )
            ok = False
    if len(set(bins)) != len(bins):
        issues.error("metadata.butler.requires.bins", "lists a command twice")
        ok = False
    return butler if ok else None


def check_required_skills(skills, name: str | None, issues: Issues) -> None:
    """`requires.skills`: at most MAX_REQUIRED_SKILLS skill names, each once, never the
    skill itself. Whether each one is LISTED needs the whole listing — --all
    (check_listed_skill_dependencies) and build_index.py ask that, not this."""
    field = "metadata.butler.requires.skills"
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        issues.error(field, "must be an array of skill names")
        return
    if len(skills) > MAX_REQUIRED_SKILLS:
        issues.error(field, f"lists {len(skills)} skills, must be <= {MAX_REQUIRED_SKILLS}")
    for s in skills:
        if len(s) > MAX_SKILL_NAME or not SKILL_NAME_RE.match(s):
            issues.error(
                field,
                f"{s!r} is not a skill name (^[a-z0-9]+(-[a-z0-9]+)*$, at most {MAX_SKILL_NAME} characters)",
            )
    if len(set(skills)) != len(skills):
        issues.error(field, "lists a skill twice")
    if name is not None and name in skills:
        issues.error(field, f"names {name!r}, the skill itself — a skill never requires itself")


def required_skills(butler) -> list[str]:
    """The well-formed names in a `metadata.butler` block's `requires.skills`, in order
    and each once; [] when the field is absent or not a list. This is what the
    listing-wide checks walk — check_required_skills reports everything else."""
    requires = butler.get("requires") if isinstance(butler, dict) else None
    skills = requires.get("skills") if isinstance(requires, dict) else None
    out: list[str] = []
    for s in skills if isinstance(skills, list) else []:
        if isinstance(s, str) and len(s) <= MAX_SKILL_NAME and SKILL_NAME_RE.match(s) and s not in out:
            out.append(s)
    return out


def check_skill_name(name: str, skill_dir: Path, standalone: bool, reserved: set[str], maintainer: bool, issues: Issues) -> None:
    if len(name) > MAX_SKILL_NAME:
        issues.error("name", f"must be at most {MAX_SKILL_NAME} characters, got {len(name)}")
    if not SKILL_NAME_RE.match(name):
        issues.error(
            "name",
            f"must match Mastra's skill-name rule ^[a-z0-9]+(-[a-z0-9]+)*$ (lowercase, digits, "
            f"single hyphens, none leading or trailing), got {name!r} — Mastra drops a skill that breaks it",
        )
        return
    if _YAML_NON_STRING_RE.match(name):
        issues.error("name", f"YAML reads {name!r} as a number or date, not a string — Mastra drops the skill")
    if not standalone and name != skill_dir.name:
        issues.error(
            "name",
            f"frontmatter name {name!r} must equal directory name {skill_dir.name!r} "
            "(the skills.json entry, and the directory the skill is installed in)",
        )
    if name in reserved:
        issues.error("name", f"{name!r} is reserved (schema/reserved-names.json)")
    if name.startswith(CONTAINER_PREFIX):
        issues.error(
            "name",
            f"{name!r} uses the '{CONTAINER_PREFIX}' prefix, which is the container's bundled-command "
            f"namespace — skills may never use it; team skills use '{MAINTAINER_PREFIX}'",
        )
    elif name.startswith(MAINTAINER_PREFIX) and not maintainer:
        issues.error(
            "name",
            f"{name!r} uses the maintainer-only '{MAINTAINER_PREFIX}' prefix; pass --maintainer or set MAINTAINER=1 to publish it",
        )


def skill_published_files(skill_dir: Path) -> list[str]:
    """What a skill publishes: SKILL.md, then every references/**/*.md whose path is
    a plain URL path, sorted. Everything else in the repo stays in the repo."""
    files = ["SKILL.md"]
    ref_root = skill_dir / "references"
    if ref_root.is_dir() and not ref_root.is_symlink():
        found: list[str] = []
        for root, dirs, names in os.walk(ref_root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for n in names:
                p = Path(root) / n
                if p.is_symlink() or not p.is_file():
                    continue
                rel = p.relative_to(skill_dir).as_posix()
                if SKILL_REFERENCE_RE.match(rel):
                    found.append(rel)
        files.extend(sorted(found))
    return files


def check_skill_layout(skill_dir: Path, issues: Issues) -> None:
    """SKILL.md, README.md and CHANGELOG.md at the root; the same tree rules as a
    template (no symlinks, no nested repos, 50 files / 1 MB)."""
    for name in SKILL_REQUIRED_FILES:
        if not (skill_dir / name).is_file():
            issues.error("layout", f"missing required file: {name}")
    count = 0
    total = 0
    for p, rel in iter_tree(skill_dir):
        if p.is_symlink():
            issues.error("layout", f"symlink not allowed: {rel}")
            continue
        if p.name == ".gitmodules":
            issues.error("layout", f"nested submodules not allowed: {rel}")
            continue
        if p.name == ".git":
            issues.error("layout", f"nested git repository not allowed: {rel}")
            continue
        if p.is_file():
            count += 1
            total += p.stat().st_size
            if rel.startswith("references/"):
                if not rel.endswith(".md"):
                    issues.warn("layout", f"{rel} is not published — only references/**/*.md reach the butler")
                elif not SKILL_REFERENCE_RE.match(rel):
                    issues.error(
                        "layout",
                        f"{rel}: a published reference path is letters, digits, '.', '_' and '-' "
                        "segments that do not start with '.'",
                    )
    if count > MAX_TREE_FILES:
        issues.error("layout", f"{count} files, must be <= {MAX_TREE_FILES}")
    if total > MAX_TREE_BYTES:
        issues.error("layout", f"{total} bytes in total, must be <= {MAX_TREE_BYTES}")
    if (skill_dir / "duty.py").exists():
        issues.error(
            "layout",
            "duty.py in a skill is never published or run — a standing program is a duty "
            "template (recipe.json + duty.py) listed in templates.json",
        )
    for sub in ("scripts", "assets"):
        if (skill_dir / sub).is_dir():
            issues.warn("layout", f"{sub}/ is not published — only SKILL.md and references/**/*.md reach the butler")
    for tool in TOOLING_FILES:
        if (skill_dir / tool).is_file():
            issues.warn("layout", f"{tool} looks like downloaded hub tooling — keep it out of the commit")
    readme = skill_dir / "README.md"
    if readme.is_file() and not readme.read_text(encoding="utf-8", errors="replace").strip():
        issues.error("README.md", "must not be empty")


def check_skill_changelog(skill_dir: Path, version: str | None, issues: Issues) -> None:
    changelog = skill_dir / "CHANGELOG.md"
    if not changelog.is_file() or not version or not SEMVER_RE.match(version):
        return  # reported by check_skill_layout / the version rule
    text = changelog.read_text(encoding="utf-8", errors="replace")
    entry = re.compile(r"^#{1,6}[ \t]+\[?v?" + re.escape(version) + r"\]?(?![0-9A-Za-z.])", re.M)
    if not entry.search(text):
        issues.error("CHANGELOG.md", f"no entry for {version} — add a '## {version}' heading saying what changed")


def iter_markdown(text: str, first_lineno: int = 1):
    """Yield (lineno, line, kind, lang) for every line: kind is "text", "open" or
    "close" (a code fence) or "code" (a line inside a fenced block, whose info-string
    language is `lang`). A block still open at the end yields a final
    ("unclosed", <line it opened on>)."""
    fence: tuple[str, int, str, int] | None = None  # (char, length, lang, opened on)
    for i, line in enumerate(text.splitlines()):
        lineno = first_lineno + i
        if fence is None:
            m = FENCE_RE.match(line)
            if m:
                marker = m.group(1)
                fence = (marker[0], len(marker), m.group(2).lower(), lineno)
                yield lineno, line, "open", fence[2]
            else:
                yield lineno, line, "text", None
            continue
        stripped = line.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= fence[1]:
            yield lineno, line, "close", fence[2]
            fence = None
        else:
            yield lineno, line, "code", fence[2]
    if fence is not None:
        yield fence[3], "", "unclosed", fence[2]


def shell_command_lines(block: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The logical command lines of one fenced shell block: comments, blank lines and
    heredoc bodies dropped, backslash continuations joined, a `$ ` prompt removed."""
    out: list[tuple[int, str]] = []
    heredoc: str | None = None
    i = 0
    while i < len(block):
        lineno, raw = block[i]
        i += 1
        text = raw.strip()
        if heredoc is not None:
            if text == heredoc:
                heredoc = None
            continue
        if text.startswith("$ "):
            text = text[2:].strip()
        if not text or text.startswith("#") or text == "$":
            continue
        while text.endswith("\\") and i < len(block):
            text = text[:-1].rstrip() + " " + block[i][1].strip()
            i += 1
        m = HEREDOC_RE.search(text)
        if m:
            heredoc = m.group(2)
        out.append((lineno, text))
    return out


def split_simple_commands(line: str) -> list[tuple[str | None, list[str]]]:
    """Split one logical shell line into (separator before it, argv) for each simple
    command in it, on `|`, `&&`, `||`, `;` and `&` — so a pipeline cannot smuggle a
    second command past the allowlist."""
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError:  # an unbalanced quote: fall back to whitespace
        tokens = line.split()
    commands: list[tuple[str | None, list[str]]] = [(None, [])]
    for tok in tokens:
        is_separator = tok in SHELL_SEPARATORS or (
            tok and set(tok) <= set("();<>|&") and ("|" in tok or ";" in tok)
        )
        if is_separator:
            commands.append((tok, []))
        else:
            commands[-1][1].append(tok)
    return [(sep, argv) for sep, argv in commands if argv]


def is_money_command(argv: list[str]) -> bool:
    """acp trade, acp wallet send-transaction, acp card, bevo-send, app-checkout checkpoint."""
    first, args = argv[0], argv[1:]
    if first == "bevo-send":
        return True
    if first == "app-checkout":
        return args[:1] == ["checkpoint"]
    if first == "acp":
        positionals = [a for a in args if not a.startswith("-")]
        group = positionals[0] if positionals else None
        sub = positionals[1] if len(positionals) > 1 else None
        return group in ("trade", "card") or (group == "wallet" and sub == "send-transaction")
    return False


def check_skill_command(argv: list[str], loc: str, issues: Issues, after: str | None = None) -> bool:
    """One simple command from a shell block against the allowlist and its subcommand
    table. True when it may run. `after` is the shell operator that precedes it."""
    first, args = argv[0], argv[1:]
    if after == "|":
        context = " (after a `|` — every command in a pipeline must be allowed too; spell alternatives out in prose, never as a|b)"
    elif after:
        context = f" (after `{after}` — every command on the line must be allowed too)"
    else:
        context = ""
    if FORBIDDEN_SKILL_COMMAND_RE.match(first):
        issues.error(
            "command-allowlist",
            f"{loc}: {first!r} is forbidden in a skill — reads go through bevo-read, chain reads "
            f"through bevo-rpc, and a skill runs no code of its own{context}",
        )
        return False
    if first not in SKILL_COMMANDS:
        issues.error(
            "command-allowlist",
            f"{loc}: {first!r} is not a command a skill may run (one of {', '.join(sorted(SKILL_COMMANDS))}){context}",
        )
        return False
    if first == "acp":
        positionals = [a for a in args if not a.startswith("-")]
        if not positionals:
            issues.error(
                "command-allowlist",
                f"{loc}: a bare `acp` / `acp --help` — name the command group; a skill never sends the butler exploring the CLI",
            )
            return False
        group = positionals[0]
        sub = positionals[1] if len(positionals) > 1 else None
        if group in ACP_REFUSED_GROUPS:
            issues.error(
                "command-allowlist",
                f"{loc}: `acp {group}` is refused by the container's acp wrapper — it needs a signer the butler does not hold",
            )
            return False
        if group not in ACP_GROUPS:
            issues.error(
                "command-allowlist",
                f"{loc}: `acp {group}` is not an acp command group the container lets through (one of {', '.join(sorted(ACP_GROUPS))})",
            )
            return False
        if group == "agent" and sub is not None and sub not in ACP_AGENT_SUBCOMMANDS:
            issues.error(
                "command-allowlist",
                f"{loc}: `acp agent {sub}` is refused by the container's acp wrapper (allowed: {', '.join(sorted(ACP_AGENT_SUBCOMMANDS))})",
            )
            return False
        return True
    subs = SKILL_SUBCOMMANDS.get(first)
    if subs is not None:
        if not args:
            issues.error("command-allowlist", f"{loc}: `{first}` needs a subcommand (one of {', '.join(sorted(subs))})")
            return False
        if args[0] not in subs:
            issues.error(
                "command-allowlist",
                f"{loc}: `{first} {args[0]}` is not one of {first}'s subcommands ({', '.join(sorted(subs))})",
            )
            return False
    return True


def check_shell_block(block: list[tuple[int, str]], rel: str, issues: Issues) -> list[tuple[int, str, list[str], bool]]:
    """Every allowed simple command in one shell block: (lineno, line, argv, is_money)."""
    found: list[tuple[int, str, list[str], bool]] = []
    for lineno, line in shell_command_lines(block):
        loc = f"{rel} line {lineno}"
        if "$(" in line or "`" in line or "<(" in line or ">(" in line:
            issues.error("command-allowlist", f"{loc}: command substitution is not allowed in a skill: {line[:80]!r}")
            continue
        for after, argv in split_simple_commands(line):
            if check_skill_command(argv, loc, issues, after):
                found.append((lineno, line, argv, is_money_command(argv)))
    return found


def check_skill_body(body: str, body_line: int, money_moving: bool | None, issues: Issues) -> tuple[set[str], list[tuple[int, str]]]:
    """Sections, numbered steps and shell blocks of SKILL.md's body.

    Returns (commands used, money command lines). A money command must sit in a
    [FIXED] step of `## Procedure`: the step is the numbered line above the block, and
    any heading ends it."""
    if len(body) > MAX_SKILL_BODY_CHARS:
        issues.error("body", f"body is {len(body)} chars, must be <= {MAX_SKILL_BODY_CHARS}")

    sections: list[tuple[str, int]] = []
    section_lines: dict[str, list[str]] = {}
    current: str | None = None
    marker: str | None = None  # the current Procedure step's marker
    steps = 0
    block: list[tuple[int, str]] = []
    used: set[str] = set()
    money: list[tuple[int, str]] = []

    def close_block(lang: str) -> None:
        if lang not in SHELL_LANGS:
            return
        for lineno, line, argv, is_money in check_shell_block(block, "SKILL.md", issues):
            used.add(argv[0])
            if is_money:
                money.append((lineno, line))
                if current != PROCEDURE_SECTION or marker != "FIXED":
                    issues.error(
                        "steps",
                        f"SKILL.md line {lineno}: a money command must sit inside a [FIXED] step of "
                        f"`## {PROCEDURE_SECTION}`: {line[:80]!r}",
                    )

    for lineno, line, kind, lang in iter_markdown(body, body_line):
        if kind == "open":
            block = []
            continue
        if kind == "code":
            block.append((lineno, line))
            if current is not None:
                section_lines[current].append(line)
            continue
        if kind in ("close", "unclosed"):
            if kind == "unclosed":
                issues.error("body", f"SKILL.md line {lineno}: code fence is never closed — it swallows the rest of the file")
            close_block(lang)
            block = []
            continue
        heading = HEADING_RE.match(line)
        if heading:
            marker = None
            if len(heading.group(1)) == 2:
                current = re.sub(r"[ \t]+#+$", "", heading.group(2)).strip()
                sections.append((current, lineno))
                section_lines.setdefault(current, [])
            continue
        if current is not None:
            section_lines[current].append(line)
        if current == PROCEDURE_SECTION and STEP_RE.match(line):
            steps += 1
            m = STEP_MARKER_RE.search(line)
            if m:
                marker = m.group(1)
            else:
                marker = None
                issues.error("steps", f"SKILL.md line {lineno}: numbered step missing [FIXED]/[ADAPT] marker: {line.strip()[:80]!r}")

    titles = [title for title, _ in sections]
    for title, lineno in sections:
        if title in SKILL_SECTIONS:
            continue
        hint = RETIRED_SECTIONS.get(title.lower())
        if hint is None:
            near = next((s for s in SKILL_SECTIONS if s.lower() == title.lower()), None)
            hint = f"did you mean `## {near}`?" if near else "put extra material under a `###` subsection of one of the seven"
        issues.error("sections", f"SKILL.md line {lineno}: `## {title}` is not a skill section — {hint}")
    for title in SKILL_SECTIONS:
        count = titles.count(title)
        if count == 0:
            issues.error("sections", f"missing required section `## {title}`")
        elif count > 1:
            issues.error("sections", f"`## {title}` appears {count} times")
    present = [t for t in titles if t in SKILL_SECTIONS]
    ordered = [t for t in SKILL_SECTIONS if t in present]
    first_seen = list(dict.fromkeys(present))
    if first_seen != ordered:
        issues.error(
            "sections",
            f"sections out of order: expected {' > '.join(ordered)}, found {' > '.join(first_seen)}",
        )
    if PROCEDURE_SECTION in titles and steps == 0:
        issues.error("steps", f"`## {PROCEDURE_SECTION}` has no numbered steps (each one [FIXED] or [ADAPT])")
    if money_moving and IDEMPOTENCY_SECTION in section_lines:
        if "do not re-run" not in " ".join(" ".join(section_lines[IDEMPOTENCY_SECTION]).split()).lower():
            issues.error(
                "sections",
                f"`## {IDEMPOTENCY_SECTION}` of a moneyMoving skill must say 'do not re-run' — "
                "a retried money command can spend twice",
            )
    return used, money


def check_reference_commands(rel: str, text: str, issues: Issues) -> set[str]:
    """Shell blocks in a published reference file: the same allowlist, and no money
    command at all — those belong in a [FIXED] step of SKILL.md."""
    used: set[str] = set()
    block: list[tuple[int, str]] = []
    for lineno, line, kind, lang in iter_markdown(text):
        if kind == "open":
            block = []
        elif kind == "code":
            block.append((lineno, line))
        elif kind in ("close", "unclosed"):
            if kind == "unclosed":
                issues.error("body", f"{rel} line {lineno}: code fence is never closed")
            if lang in SHELL_LANGS:
                for n, cmd, argv, is_money in check_shell_block(block, rel, issues):
                    used.add(argv[0])
                    if is_money:
                        issues.error(
                            "steps",
                            f"{rel} line {n}: a money command belongs in a [FIXED] step of SKILL.md's "
                            f"`## {PROCEDURE_SECTION}`, not in a reference: {cmd[:80]!r}",
                        )
            block = []
    return used


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def lint_skill_text(rel: str, text: str, issues: Issues, prose_skip_lines: frozenset[int] = frozenset()) -> None:
    """The injection and safety lints over one published file. `prose_skip_lines`
    (1-based) are left out of the override-phrase and retired-runtime lints — the
    metadata line, whose keys have their own errors."""
    lint_skill_safety(rel, text, issues)
    # Split as parse_skill_md does ("\n" only), so a skipped line number means the same line.
    prose = "\n".join(
        "" if n in prose_skip_lines else line for n, line in enumerate(text.split("\n"), start=1)
    )
    lint_skill_prose(prose, issues, lambda idx: f"{rel} line {_line_of(prose, idx)}")


def lint_skill_safety(rel: str, text: str, issues: Issues) -> None:
    """Secrets, URLs, invisible characters, raw addresses and scaffold placeholders."""
    for pat in SECRET_PATTERNS:
        m = pat.search(text)
        if m:
            issues.error("secrets-lint", f"{rel} line {_line_of(text, m.start())}: looks like a credential: {m.group(0)[:12]}...")
    for m in URL_RE.finditer(text):
        url = m.group(0)
        if not any(url == p or url.startswith(p + "/") for p in ALLOWED_URL_PREFIXES):
            issues.error(
                "url-lint",
                f"{rel} line {_line_of(text, m.start())}: disallowed URL {url!r} (only github.com/Virtual-Protocol links allowed)",
            )
    for ch, what in INVISIBLE_CHARS.items():
        idx = text.find(ch)
        if idx != -1:
            issues.error(
                "invisible-char-lint",
                f"{rel} line {_line_of(text, idx)}: contains {what} — it can hide a code-fence break "
                "or reorder what a reviewer reads; remove it",
            )
    m = RAW_ADDRESS_RE.search(text)
    if m:
        issues.error(
            "address-lint",
            f"{rel} line {_line_of(text, m.start())}: raw address {m.group(0)[:10]}… — an address "
            "comes from the owner or a read, never from a skill's text",
        )
    m = PLACEHOLDER_RE.search(text)
    if m:
        issues.error("scaffold", f"{rel} line {_line_of(text, m.start())}: unresolved {m.group(1)} placeholder")


def lint_skill_prose(prose: str, issues: Issues, where) -> None:
    """Override phrasing and mentions of the retired runtime. `where(index)` names
    the location of a hit for the message."""
    low = prose.lower()
    for phrase in OVERRIDE_PHRASES:
        idx = low.find(phrase.lower())
        if idx != -1:
            issues.error("override-phrase-lint", f"{where(idx)}: contains forbidden phrase {phrase!r}")
    for term, why in RETIRED_RUNTIME_TERMS.items():
        idx = low.find(term)
        if idx != -1:
            issues.error(
                "retired-runtime-lint",
                f"{where(idx)}: mentions {prose[idx:idx + len(term)]!r} — {why}",
            )


def validate_skill(
    skill_dir: Path, reserved: set[str], maintainer: bool, json_mode: bool, standalone: bool = False
) -> tuple[bool, dict]:
    """Validate one skill repository. `standalone=True` takes the name from the
    frontmatter; otherwise the directory is a checkout named after its skills.json
    entry and the frontmatter name must equal it."""
    issues = Issues()
    skill_md = skill_dir / "SKILL.md"
    check_skill_layout(skill_dir, issues)
    if not skill_md.is_file():
        return False, {"skill": skill_dir.name, "kind": "skill", "errors": issues.errors, "warnings": issues.warnings}

    text = skill_md.read_text(encoding="utf-8", errors="replace")
    fm = parse_skill_md(text, issues)
    if fm is None:
        return False, {"skill": skill_dir.name, "kind": "skill", "errors": issues.errors, "warnings": issues.warnings}

    for key in SKILL_FRONTMATTER_KEYS:
        if key not in fm["_keys"]:
            issues.error(key, "required field missing")
    name = fm["name"]
    if isinstance(name, str):
        check_skill_name(name, skill_dir, standalone, reserved, maintainer, issues)
    version = fm["version"]
    if isinstance(version, str) and not SEMVER_RE.match(version):
        issues.error("version", f"must be semver X.Y.Z, got {version!r}")
    description = fm["description"]
    if isinstance(description, str):
        if not description.strip():
            issues.error("description", "must not be empty")
        elif len(description) > MAX_SKILL_DESCRIPTION:
            issues.error("description", f"must be <= {MAX_SKILL_DESCRIPTION} chars, got {len(description)}")
        elif re.search(r"[\x00-\x1f\x7f]", description):
            issues.error("description", "must be one line of text — no control characters")

    butler = (
        check_skill_metadata(fm["metadata"], issues, name if isinstance(name, str) else None)
        if fm["metadata"] is not None else None
    )
    money_moving = butler.get("moneyMoving") if butler else None
    keywords = butler.get("keywords") if butler else []

    used, money = check_skill_body(fm["_body"], fm["_body_line"], money_moving, issues)
    skip = frozenset({fm["_metadata_line"]}) if fm["_metadata_line"] else frozenset()
    lint_skill_text("SKILL.md", text, issues, skip)
    if keywords:
        lint_skill_prose("\n".join(keywords), issues, lambda idx: "metadata.butler.keywords")

    for rel in skill_published_files(skill_dir)[1:]:
        ref_text = (skill_dir / rel).read_text(encoding="utf-8", errors="replace")
        lint_skill_text(rel, ref_text, issues)
        used |= check_reference_commands(rel, ref_text, issues)

    if money and money_moving is False:
        lineno, line = money[0]
        issues.error(
            "metadata.butler.moneyMoving",
            f"is false, but SKILL.md line {lineno} runs a money command ({line[:60]!r}) — declare moneyMoving: true",
        )
    if butler is not None:
        declared = set(butler["requires"]["bins"])
        for cmd in sorted(used - declared):
            issues.error(
                "metadata.butler.requires.bins",
                f"{cmd!r} runs in a shell block but is not declared — add it to requires.bins",
            )

    check_skill_changelog(skill_dir, version if isinstance(version, str) else None, issues)

    skill_name = name if isinstance(name, str) and name else skill_dir.name
    cost = prompt_cost(skill_name, description if isinstance(description, str) else "", f"skills/{skill_name}/SKILL.md")
    if not json_mode:
        print(f"  prompt cost (~97 + name + description + path): {cost} chars")

    return issues.ok, {
        "skill": skill_name if standalone else skill_dir.name,
        "kind": "skill",
        "errors": issues.errors,
        "warnings": issues.warnings,
        "promptCost": cost,
    }


# --- orchestration -----------------------------------------------------------------------


def check_name_matches_dir(rid: str | None, template_dir: Path, issues: Issues) -> None:
    """Registry mode only: the checkout is named after the templates.json
    entry, so the directory name is the ref a duty installs (<id>@<version>)
    and must equal recipe.json's id."""
    if rid and rid != template_dir.name:
        issues.error("id", f"recipe.json id {rid!r} must equal directory name {template_dir.name!r}")


def prompt_cost(name: str, description: str, path: str) -> int:
    # Kept from the prior format: a rough per-template prompt-budget estimate.
    return 97 + len(name) + len(description) + len(path)


def validate_template(
    template_dir: Path, reserved: set[str], maintainer: bool, json_mode: bool, standalone: bool = False
) -> tuple[bool, dict]:
    issues = Issues()
    recipe_path = template_dir / "recipe.json"
    if not recipe_path.is_file():
        issues.error("layout", "missing required file: recipe.json")
        check_layout(template_dir, issues)
        return False, {"template": template_dir.name, "errors": issues.errors, "warnings": issues.warnings}

    try:
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        issues.error("recipe.json", f"must be valid JSON: {e}")
        return False, {"template": template_dir.name, "errors": issues.errors, "warnings": issues.warnings}

    if not isinstance(recipe, dict):
        issues.error("recipe.json", "must be a JSON object")
        return False, {"template": template_dir.name, "errors": issues.errors, "warnings": issues.warnings}

    expected_id = None if standalone else template_dir.name
    check_layout(template_dir, issues)
    check_recipe_json(recipe, expected_id, issues)

    rid = recipe.get("id") if isinstance(recipe.get("id"), str) else None
    template_name = rid or template_dir.name

    if not standalone:
        check_name_matches_dir(rid, template_dir, issues)
    if rid:
        check_reserved(rid, reserved, maintainer, issues)

    check_readme(template_dir, issues)
    check_duty_py(template_dir, recipe, issues)

    full_text = ""
    for name in REQUIRED_FILES:
        p = template_dir / name
        if p.is_file():
            full_text += p.read_text(encoding="utf-8", errors="replace") + "\n"
    check_secrets_and_urls(full_text, issues)

    cost = prompt_cost(template_name, recipe.get("description", "") or "", f"templates/{template_name}/recipe.json")
    if not json_mode:
        print(f"  prompt cost (~97 + name + description + path): {cost} chars")

    return issues.ok, {
        "template": template_name if standalone else template_dir.name,
        "errors": issues.errors,
        "warnings": issues.warnings,
        "promptCost": cost,
    }


def load_registry(path: Path | None = None) -> list[dict]:
    """The `templates` list of templates.json: one {name, repo, ref} entry per template."""
    path = REGISTRY_PATH if path is None else path
    if not path.exists():
        raise SystemExit(f"{path} not found — --all only works in a checkout of the registry")
    rows = json.loads(path.read_text()).get("templates")
    # Empty is allowed, missing is not: an empty list is a legitimate registry (day one, or every template yanked); a MISSING key is a malformed file.
    if not isinstance(rows, list):
        raise SystemExit(f"{path} has no `templates` list")
    return rows


def clone_registry_templates(work_dir: Path) -> list[Path]:
    """Clone every templates.json entry at its ref into `work_dir`, one
    directory per template named after the registry entry, and return those
    directories. Nothing is checked out in this repo, so `--all` fetches what
    it validates."""
    return _clone_entries(load_registry(), work_dir)


def load_skills_registry(path: Path | None = None) -> list[dict]:
    """The `skills` list of skills.json: one {name, repo, ref} entry per skill."""
    path = SKILLS_REGISTRY_PATH if path is None else path
    if not path.exists():
        raise SystemExit(f"{path} not found — --all only works in a checkout of the registry")
    rows = json.loads(path.read_text()).get("skills")
    # Empty is allowed, missing is not — the same rule as templates.json.
    if not isinstance(rows, list):
        raise SystemExit(f"{path} has no `skills` list")
    return rows


def clone_registry_skills(work_dir: Path) -> list[Path]:
    """Clone every skills.json entry at its ref into `work_dir`, one directory per
    skill named after its entry — the name the frontmatter must then equal."""
    return _clone_entries(load_skills_registry(), work_dir)


# --- requires.skills across the listing ----------------------------------------------------
#
# The butler's hub installs a skill's required skills before the skill itself, refuses to
# remove a skill another installed skill requires, and takes a de-listed required skill's
# dependents with it. So every requirement must be something the index serves, and the
# requirements must leave an order to install in. A single checkout cannot answer either;
# --all asks it of the listing, and build_index.py of what one build publishes.


def _requirement_cycle(start: str, graph: dict[str, list[str]]) -> list[str] | None:
    """The shortest requires.skills path from `start` back to itself, or None."""
    parent: dict[str, str | None] = {start: None}
    frontier = deque([start])
    while frontier:
        node = frontier.popleft()
        for nxt in graph.get(node, ()):
            if nxt == node or nxt not in graph:
                continue  # a self-requirement is refused per skill; a missing one on its own
            if nxt == start:
                path = [node]
                while parent[path[-1]] is not None:
                    path.append(parent[path[-1]])
                return [*reversed(path), start]
            if nxt not in parent:
                parent[nxt] = node
                frontier.append(nxt)
    return None


def skill_dependency_errors(graph: dict[str, list[str]], listed: set[str] | None = None) -> list[tuple[str, str]]:
    """The listing-wide requires.skills rules, over skills published together.

    `graph` maps each skill to the skills it requires; `listed` is every name
    skills.json lists (by default the graph's own). Returns (skill, message) pairs,
    by skill: a required skill must be in the graph — a butler installs it first, so
    one the index does not serve can never install (a name that is listed but not in
    the graph is one this build does not publish: its current version is yanked) —
    and no requirements may form a cycle, which leaves no order to install in."""
    listed = set(graph) if listed is None else listed
    problems: list[tuple[str, str]] = []
    for name in sorted(graph):
        for req in graph[name]:
            if req == name or req in graph:
                continue
            if req in listed:
                problems.append((name, (
                    f"{req!r} is listed in skills.json but this build does not publish it (its current "
                    f"version is yanked) — a butler installs a required skill first; publish a version of "
                    f"{req!r} that is not yanked, or drop it from requires.skills"
                )))
            else:
                problems.append((name, (
                    f"{req!r} is not listed in skills.json — a butler installs a required skill first; "
                    f"list {req!r} too, or drop it from requires.skills"
                )))
        cycle = _requirement_cycle(name, graph)
        if cycle:
            problems.append((name, f"forms a cycle ({' → '.join(cycle)}) — there is no order to install them in"))
    return problems


def read_required_skills(skill_dir: Path) -> list[str]:
    """requires.skills of the SKILL.md in `skill_dir`, as required_skills() reads it;
    [] when there is no SKILL.md or its metadata does not parse (validate_skill says why)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return []
    fm = parse_skill_md(skill_md.read_text(encoding="utf-8", errors="replace"), Issues())
    metadata = fm.get("metadata") if fm else None
    return required_skills(metadata.get("butler")) if isinstance(metadata, dict) else []


def check_listed_skill_dependencies(skill_dirs: list[Path]) -> dict[str, list[str]]:
    """--all: hold the listed skills to each other (skill_dependency_errors). Each
    checkout is named after its skills.json entry, so the directory names ARE the
    listing. Returns {name: ["metadata.butler.requires.skills: ...", ...]}."""
    graph = {d.name: read_required_skills(d) for d in skill_dirs}
    out: dict[str, list[str]] = {}
    for name, msg in skill_dependency_errors(graph):
        out.setdefault(name, []).append(f"metadata.butler.requires.skills: {msg}")
    return out


def _clone_entries(rows: list[dict], work_dir: Path) -> list[Path]:
    dirs: list[Path] = []
    for entry in rows:
        name, repo, ref = entry["name"], entry["repo"], entry.get("ref") or "main"
        dest = work_dir / name
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", "--branch", ref, repo, str(dest)],
                capture_output=True, text=True, check=True, timeout=300,
            )
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"cannot clone {repo} at {ref!r}: {e.stderr.strip() or e}")
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SystemExit(f"cannot clone {repo} at {ref!r}: {e}")
        dirs.append(dest)
    return sorted(dirs)


def load_reserved() -> set[str]:
    """schema/reserved-names.json when this file runs from a registry checkout,
    otherwise (the standalone copy) the embedded mirror."""
    if RESERVED_PATH.exists():
        data = json.loads(RESERVED_PATH.read_text())
        return set(data.get("reserved", []))
    return set(RESERVED_NAMES_BUILTIN)


def detect_kind(path: Path) -> str:
    """"skill" (SKILL.md, no recipe.json), "template" (anything else — a directory
    with neither file gets the template checks, which report recipe.json missing),
    or "both", which is refused."""
    has_skill = (path / "SKILL.md").is_file()
    has_recipe = (path / "recipe.json").is_file()
    if has_skill and has_recipe:
        return "both"
    return "skill" if has_skill else "template"


def validate_path(
    path: Path, reserved: set[str], maintainer: bool, json_mode: bool,
    standalone: bool = False, expected_kind: str | None = None,
) -> tuple[bool, dict]:
    """Validate one repository as whichever kind it is. `expected_kind` is the listing
    it came from under --all: a templates.json entry must be a template, a skills.json
    entry a skill."""
    kind = detect_kind(path)
    if kind == "both":
        issues = Issues()
        issues.error(
            "layout",
            "holds both SKILL.md and recipe.json — a repository is one kind: a skill (SKILL.md) "
            "or a duty template (recipe.json)",
        )
        return False, {"path": path.name, "kind": "unknown", "errors": issues.errors, "warnings": issues.warnings}
    if expected_kind is not None and kind != expected_kind:
        issues = Issues()
        if expected_kind == "skill":
            issues.error(
                "layout",
                "listed in skills.json but has no SKILL.md at its root"
                + (" — it holds recipe.json, so it belongs in templates.json" if (path / "recipe.json").is_file() else ""),
            )
            return False, {"skill": path.name, "kind": "skill", "errors": issues.errors, "warnings": issues.warnings}
        issues.error(
            "layout",
            "listed in templates.json but holds SKILL.md and no recipe.json — a skill belongs in skills.json",
        )
        return False, {"template": path.name, "errors": issues.errors, "warnings": issues.warnings}
    if kind == "skill":
        return validate_skill(path, reserved, maintainer, json_mode, standalone=standalone)
    return validate_template(path, reserved, maintainer, json_mode, standalone=standalone)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate butler-skills repositories: skills (SKILL.md) and duty templates (recipe.json)."
    )
    parser.add_argument(
        "paths", nargs="*", metavar="dir",
        help="skill or template directories (registry mode) or any skill/template repository (--standalone)",
    )
    parser.add_argument("--all", action="store_true", help="clone and validate every entry in templates.json and skills.json")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="treat each path as a repository checkout: the name comes from recipe.json / SKILL.md, not the directory",
    )
    parser.add_argument("--maintainer", action="store_true", help="allow the maintainer-only butler- prefix (bevo- is always refused)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON output")
    args = parser.parse_args()

    if args.all and args.standalone:
        parser.error("--all is registry mode; it cannot be combined with --standalone")

    maintainer = args.maintainer or os.environ.get("MAINTAINER") == "1"
    reserved = load_reserved()

    results = []
    all_ok = True
    with ExitStack() as stack:
        targets: list[tuple[Path, str | None]] = []
        # --all only: requires.skills problems across the listing, by skill name.
        dependency_errors: dict[str, list[str]] = {}
        if args.all:
            work_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="butler-skills-validate-")))
            (work_dir / "templates").mkdir()
            (work_dir / "skills").mkdir()
            targets.extend((d, "template") for d in clone_registry_templates(work_dir / "templates"))
            skill_dirs = clone_registry_skills(work_dir / "skills")
            targets.extend((d, "skill") for d in skill_dirs)
            dependency_errors = check_listed_skill_dependencies(skill_dirs)
        for s in args.paths:
            targets.append((Path(s).resolve(), None))

        if not targets:
            # `--all` over an empty registry is a pass, not a usage error: the
            # registry is allowed to list nothing (day one, or every entry
            # yanked), and CI runs this on every PR. Without the distinction a
            # deliberately empty listing fails the gate that exists to check
            # what it lists.
            if args.all:
                print("registry lists no templates and no skills — nothing to validate")
                return 0
            parser.error("no directories given; pass a path, --standalone <dir>, or --all")

        for t, expected_kind in targets:
            if not args.json:
                print(f"validating {t.relative_to(REPO_ROOT) if t.is_relative_to(REPO_ROOT) else t.name} ...")
            ok, result = validate_path(
                t, reserved, maintainer, args.json, standalone=args.standalone, expected_kind=expected_kind
            )
            if expected_kind == "skill" and dependency_errors.get(t.name):
                result["errors"].extend(dependency_errors[t.name])
                ok = False
            all_ok = all_ok and ok
            results.append(result)
            if not args.json:
                for e in result["errors"]:
                    print(f"  ERROR {e}")
                for w in result["warnings"]:
                    print(f"  WARN  {w}")
                if ok:
                    print("  OK")

    if args.json:
        print(json.dumps({"ok": all_ok, "results": results}, indent=2))

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
