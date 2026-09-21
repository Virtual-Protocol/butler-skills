#!/usr/bin/env python3
"""validate.py — the butler-skills CI validator.

Usage:
    scripts/validate.py <dir> [<dir> ...]        # registry mode: a checkout of a listed template
    scripts/validate.py --all                    # clone and validate every templates.json entry
    scripts/validate.py --all --maintainer       # allow the butler- prefix / reserved-adjacent ids
    scripts/validate.py <dir> --json             # machine-readable output
    scripts/validate.py --standalone <dir>       # any directory holding one template (a template repo)

Standalone copy (no registry checkout needed — publish.yml puts this exact file on the
Pages site; a template author runs it from their own repo):

    curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
    python3 validate.py --standalone .

This file is therefore a single-file tool: Python 3.11 stdlib only, no imports from the
other scripts, and the reserved-id list is embedded (schema/reserved-names.json is read
when it exists next to a registry checkout; tests assert the two agree).

A duty template is a bundle at the root of its own repository: `recipe.json`,
`duty.py`, `README.md`. No frontmatter, no SKILL.md — a duty is one Python
program plus the manifest that names it, describes its settings and lists
what triggers it accepts.

Two modes:

  registry (default) — the directory is a checkout of a template this registry
  lists, named after the entry in templates.json, so recipe.json's `id` must
  equal the directory name: that is the name a duty is created with
  (`duty_create {recipe: "<id>@<version>", params: {...}}`). `--all` clones
  every templates.json entry at its ref into a temporary directory and
  validates those. The link itself — that `repo` is an
  https://github.com/<owner>/<repo> URL and that `ref` resolves — is
  scripts/check_registry.py's job, not this file's.

  --standalone — the directory is a template repository checked out anywhere.
  The id comes from recipe.json alone (it only has to be a valid template id);
  every other rule is identical, so a template that passes here passes the
  registry PR.

Python 3.11 stdlib only. No network access except `--all`, which clones the
templates.json entries (no template is checked out in this repo). Exits 1 on
any failing check and prints one field-by-field message per failure. This
script is the source of truth for what a passing PR looks like.
"""
from __future__ import annotations

import ast
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
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
    "acp-cli",
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
    dirs: list[Path] = []
    for entry in load_registry():
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one or more butler-skills duty-template directories.")
    parser.add_argument("templates", nargs="*", help="template directories (registry mode) or any template directory (--standalone)")
    parser.add_argument("--all", action="store_true", help="clone and validate every template listed in templates.json")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="treat each path as a template repository checkout: the id comes from recipe.json, not the directory",
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
        targets: list[Path] = []
        if args.all:
            work_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="butler-skills-validate-"))
            targets.extend(clone_registry_templates(Path(work_dir)))
        for s in args.templates:
            targets.append(Path(s).resolve())

        if not targets:
            # `--all` over an empty registry is a pass, not a usage error: the
            # registry is allowed to list nothing (day one, or every template
            # yanked), and CI runs this on every PR. Without the distinction a
            # deliberately empty templates.json fails the gate that exists to
            # check the templates it lists.
            if args.all:
                print("registry lists no templates — nothing to validate")
                return 0
            parser.error("no templates given; pass a path, --standalone <dir>, or --all")

        for t in targets:
            if not args.json:
                print(f"validating {t.relative_to(REPO_ROOT) if t.is_relative_to(REPO_ROOT) else t.name} ...")
            ok, result = validate_template(t, reserved, maintainer, args.json, standalone=args.standalone)
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
