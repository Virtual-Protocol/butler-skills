#!/usr/bin/env python3
"""validate.py — the butler-skills CI validator.

Usage:
    scripts/validate.py skills/<name> [skills/<name> ...]
    scripts/validate.py --all
    scripts/validate.py --all --maintainer      # allow the bevo- prefix / reserved-adjacent names
    scripts/validate.py skills/<name> --json     # machine-readable output

Python 3.11 stdlib only. No network access. Exits 1 on any failing check and
prints one field-by-field message per failure. This script is the source of
truth for what a passing PR looks like; scripts/check_selectors.mjs (invoked
here when node is available) covers viem-based selector recomputation.
"""
from __future__ import annotations

import ast
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
SCHEMA_PATH = REPO_ROOT / "schema" / "skill-frontmatter.schema.json"
RESERVED_PATH = REPO_ROOT / "schema" / "reserved-names.json"

MAX_DESCRIPTION = 160
MAX_BODY_CHARS = 12000
MAX_BUNDLE_BYTES = 200 * 1024

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
PARAM_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ROUTE_RE = re.compile(r"^(GET|POST|PATCH|DELETE) /butler-(read|exec)/[A-Za-z0-9/_:.-]+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
GATES = {"canPerp", "canSwap", "canStock", "canFiat", "canOnramp"}
OPENCLAW_METADATA_KEYS = {"emoji", "homepage", "requires"}

# The Butler toolbox (README §3 / SKILL_STANDARD.md table). First token of
# every command line in a skill's shell code blocks must appear here (or be
# a validated subcommand of bevo-read / bevo-automation / bevo-hub / acp).
TOOLBOX_FIRST_TOKENS = {
    "bevo-notify",
    "bevo-rpc",
    "bevo-read",
    "bevo-send",
    "acp",
    "bevo-automation",
    "bevo-hub",
    "bevo-x",
    "bevo-location",
    "bevo-sms",
    "web-checkout",
    "node",  # viem one-liners for calldata encoding
}

BEVO_READ_SUBCOMMANDS = {
    "messages",
    "channel-messages",
    "participants",
    "user",
    "assets",
    "me",
    "groups",
    "summary",
    "search",
    "trade-activity",
    "trade-executions",
    "wallet-transfers",
    "request",
    "card-budget",
    "token-search",
    "token-balance",
}

BEVO_AUTOMATION_SUBCOMMANDS = {
    "create",
    "validate",
    "rehearse",
    "sample",
    "update",
    "enable",
    "disable",
    "delete",
    "list",
    "logs",
}

BEVO_HUB_SUBCOMMANDS = {"search", "show", "install", "update", "list", "remove", "set", "unset"}

ACP_SUBCOMMANDS = {
    "trade",
    "wallet",
    "card",
    "email",
    "browse",
    "client",
    "provider",
    "job",
    "offering",
    "events",
}

REQUIRED_SECTIONS = [
    "## When to use",
    "## Before you start",
    "## Customize",
    "## One-off procedure",
    "## Duty procedure",
    "## Idempotency and retries",
    "## Failure handling",
    "## Limits",
    "## Say to the owner",
]

SECRET_PATTERNS = [
    re.compile(r"\bbrt_[A-Za-z0-9]+"),
    re.compile(r"\bsk-[A-Za-z0-9]+"),
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
]

URL_RE = re.compile(r"https?://[^\s`)]+")
ALLOWED_URL_PREFIXES = ("{API_BASE}", "https://github.com/Virtual-Protocol", "https://raw.githubusercontent.com/Virtual-Protocol")

OVERRIDE_PHRASES = ["ignore previous", "ignore all previous", "override", "SOUL.md", "do not tell", "disregard your instructions"]

STEP_RE = re.compile(r"^\s*\d+\.\s")
MARKER_RE = re.compile(r"\[(FIXED|ADAPT)\]")

MONEY_COMMAND_PREFIXES = ("acp trade", "acp wallet send-transaction", "bevo-send")

# Prompt-cost formula vendored from openclaw's workspace-*.js (~97 + name + description + path).
def prompt_cost(name: str, description: str, path: str) -> int:
    return 97 + len(name) + len(description) + len(path)


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


def parse_frontmatter(text: str, issues: Issues) -> dict | None:
    """Line-oriented parse matching the openclaw SKILL.md parser: a leading
    '---' fence, then 'key: value' lines, metadata is a single JSON line."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        issues.error("frontmatter", "file must start with a '---' fence")
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        issues.error("frontmatter", "no closing '---' fence found")
        return None
    fm: dict = {}
    i = 1
    while i < end:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            issues.error("frontmatter", f"unparseable line {i + 1}: {line!r}")
            i += 1
            continue
        key, value = m.group(1), m.group(2)
        if key == "metadata":
            try:
                fm["metadata"] = json.loads(value)
            except json.JSONDecodeError as e:
                issues.error("metadata", f"must be one line of valid JSON: {e}")
                fm["metadata"] = None
        elif key == "user-invocable":
            fm["user-invocable"] = value.strip().lower() == "true"
        else:
            fm[key] = value.strip()
        i += 1
    body = "\n".join(lines[end + 1:])
    fm["_body"] = body
    return fm


def validate_schema(fm: dict, issues: Issues) -> None:
    for req in ("name", "description", "version", "metadata"):
        if req not in fm or fm.get(req) in (None, ""):
            issues.error(req, "required field missing")
    name = fm.get("name", "")
    if name and not NAME_RE.match(name):
        issues.error("name", f"must match ^[a-z0-9][a-z0-9-]{{1,63}}$, got {name!r}")
    version = fm.get("version", "")
    if version and not SEMVER_RE.match(version):
        issues.error("version", f"must be semver X.Y.Z, got {version!r}")
    description = fm.get("description", "")
    if description and len(description) > MAX_DESCRIPTION:
        issues.error("description", f"must be <= {MAX_DESCRIPTION} chars, got {len(description)}")

    metadata = fm.get("metadata")
    if not isinstance(metadata, dict):
        return
    if "bevo" not in metadata:
        issues.error("metadata.bevo", "required block missing")
        return
    bevo = metadata["bevo"]
    if not isinstance(bevo, dict):
        issues.error("metadata.bevo", "must be an object")
        return
    for req in ("tier", "modes", "moneyMoving"):
        if req not in bevo:
            issues.error(f"metadata.bevo.{req}", "required field missing")
    if bevo.get("tier") not in (None, "core", "on-demand"):
        issues.error("metadata.bevo.tier", f"must be core|on-demand, got {bevo.get('tier')!r}")
    if "modes" in bevo:
        if not isinstance(bevo["modes"], list) or not bevo["modes"]:
            issues.error("metadata.bevo.modes", "must be a non-empty array")
        else:
            for m in bevo["modes"]:
                if m not in ("one-off", "duty"):
                    issues.error("metadata.bevo.modes", f"unknown mode {m!r}")
    if "moneyMoving" in bevo and not isinstance(bevo["moneyMoving"], bool):
        issues.error("metadata.bevo.moneyMoving", "must be a bool")

    # openclaw metadata key allowlist
    openclaw = metadata.get("openclaw")
    if openclaw is not None:
        if not isinstance(openclaw, dict):
            issues.error("metadata.openclaw", "must be an object")
        else:
            extra = set(openclaw.keys()) - OPENCLAW_METADATA_KEYS
            if extra:
                issues.error("metadata.openclaw", f"forbidden keys: {sorted(extra)} (only emoji, homepage, requires.bins allowed)")
            if "requires" in openclaw and isinstance(openclaw["requires"], dict):
                extra2 = set(openclaw["requires"].keys()) - {"bins"}
                if extra2:
                    issues.error("metadata.openclaw.requires", f"forbidden keys: {sorted(extra2)} (only bins allowed)")

    # params
    params = bevo.get("params", [])
    if params:
        seen = set()
        for p in params:
            pname = p.get("name", "")
            if not PARAM_NAME_RE.match(pname):
                issues.error("metadata.bevo.params", f"{pname!r} must match ^[A-Z][A-Z0-9_]*$")
            if pname in seen:
                issues.error("metadata.bevo.params", f"duplicate param name {pname!r}")
            seen.add(pname)
            ptype = p.get("type")
            valid_types = {
                "usd", "number", "int", "enum", "chainIds", "chainId", "address",
                "principalId", "wallet", "principalId|wallet", "string", "bool",
            }
            if ptype not in valid_types:
                issues.error("metadata.bevo.params", f"{pname}: unknown type {ptype!r}")
            default = p.get("default")
            if default is not None and isinstance(default, (int, float)):
                if "min" in p and default < p["min"]:
                    issues.error("metadata.bevo.params", f"{pname}: default {default} < min {p['min']}")
                if "max" in p and default > p["max"]:
                    issues.error("metadata.bevo.params", f"{pname}: default {default} > max {p['max']}")
            if p.get("required") and not p.get("ask"):
                issues.error("metadata.bevo.params", f"{pname}: required param must declare 'ask'")

    # requires.routes / gates
    requires = bevo.get("requires", {})
    if isinstance(requires, dict):
        for route in requires.get("routes", []):
            if not ROUTE_RE.match(route):
                issues.error("metadata.bevo.requires.routes", f"{route!r} does not match required pattern")
        for gate in requires.get("gates", []):
            if gate not in GATES:
                issues.error("metadata.bevo.requires.gates", f"unknown gate {gate!r}")

    # web3 block
    web3 = bevo.get("web3")
    if web3 is not None and not isinstance(web3, dict):
        issues.error("metadata.bevo.web3", "must be an object")


def check_name_matches_dir(fm: dict, skill_dir: Path, issues: Issues) -> None:
    name = fm.get("name")
    if name and name != skill_dir.name:
        issues.error("name", f"frontmatter name {name!r} must equal directory name {skill_dir.name!r}")


def check_reserved(fm: dict, reserved: set[str], maintainer: bool, issues: Issues) -> None:
    name = fm.get("name", "")
    if not name:
        return
    if name in reserved:
        issues.error("name", f"{name!r} is reserved (schema/reserved-names.json)")
    if name.startswith("bevo-") and not maintainer:
        issues.error("name", f"{name!r} uses the maintainer-only 'bevo-' prefix; pass --maintainer or set MAINTAINER=1 to publish it")


def check_secrets_and_urls(full_text: str, issues: Issues) -> None:
    for pat in SECRET_PATTERNS:
        m = pat.search(full_text)
        if m:
            issues.error("secrets-lint", f"looks like a credential: {m.group(0)[:12]}...")
    for m in URL_RE.finditer(full_text):
        url = m.group(0)
        if not any(url.startswith(p) for p in ALLOWED_URL_PREFIXES):
            issues.error("url-lint", f"disallowed URL {url!r} (only {{API_BASE}} and github.com/Virtual-Protocol links allowed)")
    if "\u200b" in full_text:
        issues.error(
            "invisible-char-lint",
            "file contains U+200B (zero-width space) — this can hide code-fence-breaking "
            "tricks and paste invisibly into a copy; remove it",
        )


def check_override_phrases(description: str, body: str, issues: Issues) -> None:
    haystacks = {"description": description, "body": body}
    for field, text in haystacks.items():
        low = text.lower()
        for phrase in OVERRIDE_PHRASES:
            if phrase.lower() in low:
                issues.error("override-phrase-lint", f"{field} contains forbidden phrase {phrase!r}")
    if re.search(r"0x[a-fA-F0-9]{40}", description):
        issues.error("override-phrase-lint", "description must not contain a raw wallet address")


def extract_shell_lines(body: str) -> list[str]:
    lines = []
    in_block = False
    lang = None
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            if not in_block:
                in_block = True
                lang = stripped[3:].strip().lower()
            else:
                in_block = False
                lang = None
            continue
        if in_block and lang in ("", "bash", "sh", "shell", "console"):
            if stripped and not stripped.startswith("#"):
                lines.append(stripped)
    return lines


def check_command_allowlist(body: str, issues: Issues) -> list[str]:
    money_lines = []
    for line in extract_shell_lines(body):
        first = line.split()[0] if line.split() else ""
        if first == "curl":
            issues.error("command-allowlist", f"raw curl is forbidden: {line!r}")
            continue
        if first not in TOOLBOX_FIRST_TOKENS:
            issues.error("command-allowlist", f"command {first!r} is not in the toolbox table: {line!r}")
            continue
        tokens = line.split()
        if first == "bevo-read" and len(tokens) > 1:
            sub = tokens[1]
            if sub not in BEVO_READ_SUBCOMMANDS:
                issues.error("command-allowlist", f"bevo-read subcommand {sub!r} unknown: {line!r}")
        if first == "bevo-automation" and len(tokens) > 1:
            sub = tokens[1]
            if sub not in BEVO_AUTOMATION_SUBCOMMANDS:
                issues.error("command-allowlist", f"bevo-automation subcommand {sub!r} unknown: {line!r}")
        if first == "bevo-hub" and len(tokens) > 1:
            sub = tokens[1]
            if sub not in BEVO_HUB_SUBCOMMANDS:
                issues.error("command-allowlist", f"bevo-hub subcommand {sub!r} unknown: {line!r}")
        if first == "acp":
            if len(tokens) > 1 and tokens[1] == "--help":
                issues.error("command-allowlist", "bare 'acp --help' is forbidden")
            elif len(tokens) > 1 and tokens[1] not in ACP_SUBCOMMANDS:
                issues.error("command-allowlist", f"acp subcommand {tokens[1]!r} unknown: {line!r}")
        if any(line.startswith(p) for p in MONEY_COMMAND_PREFIXES):
            money_lines.append(line)
    return money_lines


def check_sections(body: str, moneymoving: bool, issues: Issues) -> None:
    positions = []
    for section in REQUIRED_SECTIONS:
        idx = body.find(section)
        if section == "## Duty procedure" and idx == -1:
            # duty procedure only required for modes including 'duty'; presence
            # checked separately in check_frontmatter_body_consistency
            continue
        if idx == -1:
            if section == "## Idempotency and retries" and not moneymoving:
                continue
            issues.error("sections", f"missing required section {section!r}")
            continue
        positions.append((idx, section))
    ordered = [s for _, s in sorted(positions)]
    present_required = [s for s in REQUIRED_SECTIONS if s in body]
    if ordered != [s for s in REQUIRED_SECTIONS if s in present_required]:
        pass  # order re-checked precisely below
    # strict order check among sections that are present
    present_in_order = [s for s in REQUIRED_SECTIONS if body.find(s) != -1]
    actual_order = sorted(present_in_order, key=lambda s: body.find(s))
    if present_in_order != actual_order:
        issues.error("sections", f"sections out of order: expected {present_in_order}, found order {actual_order}")

    if moneymoving:
        idx = body.find("## Idempotency and retries")
        if idx != -1:
            section_text = body[idx:]
            next_h2 = section_text.find("\n## ", 3)
            section_text = section_text[:next_h2] if next_h2 != -1 else section_text
            if "do not re-run" not in section_text.lower():
                issues.error("sections", "## Idempotency and retries must contain the phrase 'do not re-run'")


def check_numbered_steps(body: str, moneymoving: bool, issues: Issues) -> None:
    lines = body.splitlines()
    current_marker = None
    in_block = False
    lang = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_block:
                in_block = True
                lang = stripped[3:].strip().lower()
            else:
                in_block = False
                lang = None
            continue
        if not in_block and STEP_RE.match(line):
            m = MARKER_RE.search(line)
            if not m:
                issues.error("steps", f"numbered step missing [FIXED]/[ADAPT] marker: {line.strip()!r}")
                current_marker = None
            else:
                current_marker = m.group(1)
            continue
        # Only real command lines (inside a fenced shell block) count as a money-moving
        # command — a prose mention like "(use `acp trade`)" must never trip this check.
        if in_block and lang in ("", "bash", "sh", "shell", "console") and moneymoving:
            if any(stripped.startswith(p) for p in MONEY_COMMAND_PREFIXES):
                if current_marker != "FIXED":
                    issues.error("steps", f"money-moving command line must be inside a [FIXED] step: {stripped!r}")


def check_bundle_size(skill_dir: Path, issues: Issues) -> None:
    total = 0
    for f in skill_dir.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
            if f.suffix in (".png", ".jpg", ".jpeg", ".gif", ".zip", ".tar", ".exe", ".bin", ".so", ".dylib"):
                issues.error("no-binaries", f"binary file not allowed: {f}")
    if total > MAX_BUNDLE_BYTES:
        issues.error("bundle-size", f"bundle is {total} bytes, must be <= {MAX_BUNDLE_BYTES}")


def check_duty_py(skill_dir: Path, params: list[dict], issues: Issues) -> None:
    duty_path = skill_dir / "duty.py"
    if not duty_path.exists():
        return
    src = duty_path.read_text()
    try:
        compile(src, str(duty_path), "exec")
    except SyntaxError as e:
        issues.error("duty.py", f"py_compile failed: {e}")
        return
    try:
        tree = ast.parse(src, filename=str(duty_path))
    except SyntaxError as e:
        issues.error("duty.py", f"ast parse failed: {e}")
        return

    param_names = {p.get("name") for p in params}
    stdlib_ok = True

    forbidden_calls = {"eval", "exec", "compile"}
    forbidden_modules = {
        "subprocess", "os.system", "socket", "urllib", "urllib.request",
        "requests", "http.client", "http",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "bevo":
                issues.error("duty.py", "forbidden: 'from bevo import ...' (use 'import bevo' and bevo.<call>)")
            elif node.module and node.module.split(".")[0] in forbidden_modules:
                issues.error("duty.py", f"forbidden import: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if alias.name == "bevo" and alias.asname:
                    issues.error("duty.py", "forbidden: aliasing 'import bevo as ...'")
                if base in forbidden_modules or alias.name in forbidden_modules:
                    issues.error("duty.py", f"forbidden import: {alias.name}")
        if isinstance(node, ast.Call):
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
                # os.system(...)
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "system":
                    issues.error("duty.py", "forbidden call: os.system(...)")
            if fname in forbidden_calls:
                issues.error("duty.py", f"forbidden call: {fname}(...)")
            # bevo.trade / bevo.execute must carry idempotency_key kwarg != None
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "bevo" and node.func.attr in ("trade", "execute"):
                    kw = {k.arg: k.value for k in node.keywords if k.arg}
                    if "idempotency_key" not in kw:
                        issues.error("duty.py", f"bevo.{node.func.attr}(...) call missing keyword idempotency_key=")
                    else:
                        val = kw["idempotency_key"]
                        if isinstance(val, ast.Constant) and val.value is None:
                            issues.error("duty.py", f"bevo.{node.func.attr}(...) idempotency_key must not be None")
        # bare except: pass
        if isinstance(node, ast.ExceptHandler):
            if node.type is None and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                issues.error("duty.py", "forbidden: bare 'except: pass'")

    # os.environ[...] / os.environ.get(...) keys must be subset of declared params
    env_keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            if _is_os_environ(node.value):
                key = _const_str(node.slice)
                if key:
                    env_keys.add(key)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and _is_os_environ(node.func.value):
                if node.args and _const_str(node.args[0]):
                    env_keys.add(_const_str(node.args[0]))
    undeclared = env_keys - param_names
    if undeclared:
        issues.error("duty.py", f"os.environ keys not declared in params: {sorted(undeclared)}")


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def check_web3(fm: dict, body: str, money_lines: list[str], issues: Issues) -> None:
    bevo = fm.get("metadata", {}).get("bevo", {}) if isinstance(fm.get("metadata"), dict) else {}
    has_send_tx = any("send-transaction" in line or "bevo.execute" in line for line in money_lines) or "send-transaction" in body or "bevo.execute(" in body
    web3 = bevo.get("web3")
    if has_send_tx and not web3:
        issues.error("web3", "skill files send-transaction / bevo.execute but declares no metadata.bevo.web3 block")
    if web3:
        for contract in web3.get("contracts", []):
            addr = contract.get("address", "")
            if not (addr.startswith("{{") or re.match(r"^0x[0-9a-fA-F]{40}$", addr)):
                issues.error("web3", f"contract {contract.get('name')!r} address {addr!r} is not a checksummed 0x40 address or a {{PARAM}} placeholder")
            for fn in contract.get("functions", []):
                sig, sel = fn.get("signature"), fn.get("selector")
                recomputed = recompute_selector(sig)
                if recomputed and recomputed != sel:
                    issues.error("web3", f"selector for {sig!r} is {sel!r}, recomputed {recomputed!r}")
                elif recomputed is None:
                    issues.warn("web3", f"could not recompute selector for {sig!r} (node/viem unavailable) — trust but verify")
        if "## Contracts" not in body:
            issues.error("web3", "web3 skill must include a '## Contracts' section")
    if "http_poll" in body and re.search(r"bevo-rpc|eth_call|eth_getBalance", body) and "GET only" not in body:
        issues.warn("web3", "if this skill pairs http_poll with an RPC read, note that http_poll is GET-only and cannot hit a node")
    for line in extract_shell_lines(body):
        if line.startswith("acp wallet send-transaction"):
            for flag in ("--chain-id", "--to", "--data", "--idempotency-key"):
                if flag not in line:
                    issues.error("web3", f"send-transaction line missing {flag}: {line!r}")


_SELECTOR_CACHE: dict[str, str | None] = {}


def recompute_selector(signature: str) -> str | None:
    """Best-effort selector recomputation via scripts/check_selectors.mjs + viem.
    Returns None (never fails the build) when node/viem is unavailable."""
    if signature in _SELECTOR_CACHE:
        return _SELECTOR_CACHE[signature]
    node = which("node")
    if not node:
        _SELECTOR_CACHE[signature] = None
        return None
    script = REPO_ROOT / "scripts" / "check_selectors.mjs"
    if not script.exists():
        _SELECTOR_CACHE[signature] = None
        return None
    try:
        out = subprocess.run(
            [node, str(script), "--signature", signature],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if out.returncode == 0:
            sel = out.stdout.strip().splitlines()[-1].strip()
            _SELECTOR_CACHE[signature] = sel
            return sel
    except Exception:
        pass
    _SELECTOR_CACHE[signature] = None
    return None


def which(name: str) -> str | None:
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(p) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def check_changelog(skill_dir: Path, issues: Issues) -> None:
    if not (skill_dir / "CHANGELOG.md").exists():
        issues.error("CHANGELOG.md", "missing")


def validate_skill(skill_dir: Path, reserved: set[str], maintainer: bool, json_mode: bool) -> tuple[bool, dict]:
    issues = Issues()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        issues.error("SKILL.md", "missing")
        return False, {"skill": skill_dir.name, "errors": issues.errors, "warnings": issues.warnings}

    text = skill_md.read_text()
    fm = parse_frontmatter(text, issues)
    if fm is None:
        return False, {"skill": skill_dir.name, "errors": issues.errors, "warnings": issues.warnings}

    body = fm.get("_body", "")

    validate_schema(fm, issues)
    check_name_matches_dir(fm, skill_dir, issues)
    check_reserved(fm, reserved, maintainer, issues)

    if len(body) > MAX_BODY_CHARS:
        issues.error("body", f"body is {len(body)} chars, must be <= {MAX_BODY_CHARS}")

    check_secrets_and_urls(text, issues)
    check_override_phrases(fm.get("description", ""), body, issues)

    bevo = fm.get("metadata", {}).get("bevo", {}) if isinstance(fm.get("metadata"), dict) else {}
    moneymoving = bool(bevo.get("moneyMoving"))
    modes = bevo.get("modes", [])
    params = bevo.get("params", [])

    money_lines = check_command_allowlist(body, issues)
    check_sections(body, moneymoving, issues)
    check_numbered_steps(body, moneymoving, issues)
    check_bundle_size(skill_dir, issues)
    check_duty_py(skill_dir, params, issues)
    check_web3(fm, body, money_lines, issues)
    check_changelog(skill_dir, issues)

    if "duty" in modes and "## Duty procedure" not in body:
        issues.error("sections", "modes includes 'duty' but '## Duty procedure' section is missing")

    if "TODO" in text:
        issues.error("scaffold", "unresolved TODO placeholder found (scaffold not filled in)")

    cost = prompt_cost(fm.get("name", ""), fm.get("description", ""), f"skills/{skill_dir.name}/SKILL.md")
    if not json_mode:
        print(f"  prompt cost (~97 + name + description + path): {cost} chars")

    return issues.ok, {
        "skill": skill_dir.name,
        "errors": issues.errors,
        "warnings": issues.warnings,
        "promptCost": cost,
    }


def load_reserved() -> set[str]:
    data = json.loads(RESERVED_PATH.read_text())
    return set(data.get("reserved", []))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one or more butler-skills skill directories.")
    parser.add_argument("skills", nargs="*", help="skills/<name> paths to validate")
    parser.add_argument("--all", action="store_true", help="validate every skill under skills/ (excluding _template)")
    parser.add_argument("--maintainer", action="store_true", help="allow the bevo- prefix and reserved-adjacent names")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON output")
    args = parser.parse_args()

    maintainer = args.maintainer or os.environ.get("MAINTAINER") == "1"
    reserved = load_reserved()

    targets: list[Path] = []
    if args.all:
        for d in sorted(SKILLS_DIR.iterdir()):
            if d.is_dir() and d.name != "_template" and (d / "SKILL.md").exists():
                targets.append(d)
    for s in args.skills:
        targets.append(Path(s).resolve())

    if not targets:
        parser.error("no skills given; pass a path or --all")

    results = []
    all_ok = True
    for t in targets:
        if not args.json:
            print(f"validating {t.relative_to(REPO_ROOT) if t.is_relative_to(REPO_ROOT) else t} ...")
        ok, result = validate_skill(t, reserved, maintainer, args.json)
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
