#!/usr/bin/env python3
"""venue.py — the checks for a site skill's venue file (venue.yaml).

A site skill (docs/site-skills/butler-site-skill-guide.md) holds, next to SKILL.md,
one `venue.yaml`: the allow-list of every request and signature the site may cause,
the steps for each action, and the daily check (`canary`). The engine on bevo-server
runs it; this file only checks it, the way validate.py checks SKILL.md.

    venue_errors(doc) -> list[str]

`doc` is the parsed YAML. Every rule here is one the guide (Part 7) or
docs/site-skills/security-rules.md names; each error says which. The rules hold the
money, not the steps: a wrong step stops a run, a wrong allow-list sends money, so
anything that widens what can be sent is an error, never a warning.

PyYAML is the one dependency outside the standard library, and only a skill with a
venue file needs it: load_venue() imports it on first use.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

VENUE_FILE = "venue.yaml"
VENUE_TEST_FILE = "venue.test.yaml"
# What a skill with a venue file publishes beside SKILL.md, for the server only:
# the butler never installs these (build_index.py puts them under the row's `venue`).
VENUE_FILES = (VENUE_FILE, VENUE_TEST_FILE)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ACTION_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")

MONEY_BY = ("session", "wallet")
LOGIN = ("wallet", "email")
CALL_METHODS = ("POST", "PUT", "PATCH", "DELETE")
CALL_KINDS = ("free", "money")
SIGNATURE_KINDS = ("sign-in", "eip712", "transaction")
# Typed args only (S4, S7): no free text, and never an address — money leaves only to
# the owner's own Butler wallet, so no action may take a destination.
ARG_TYPES = ("enum", "usd", "number", "integer", "slug", "boolean")
STEP_TYPES = (
    "call", "send-once",                                      # API steps
    "goto", "click", "fill", "wait", "click-once",            # page steps
    "pick", "check", "confirm",                               # both
)
MONEY_STEPS = ("send-once", "click-once")
FIELD_RULES = ("at-most", "within-seconds", "equals")
UINT256_MAX = 2**256 - 1


def load_venue(path: Path):
    """The parsed venue file. Raises ImportError without PyYAML, ValueError on bad YAML."""
    import yaml  # only a skill with a venue file needs it

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML: {exc}") from exc


def _is_https_origin(url) -> bool:
    if not isinstance(url, str):
        return False
    u = urlsplit(url)
    return u.scheme == "https" and bool(u.hostname) and u.path in ("", "/") and not u.query and not u.username


def _is_unlimited(value) -> bool:
    """An unlimited approve: uint256 max as a number, a decimal string or 0xff..ff."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= UINT256_MAX
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("max", "unlimited", "infinity"):
            return True
        if re.fullmatch(r"0x[0-9a-f]+", v):
            return int(v, 16) >= UINT256_MAX
        if v.isdigit():
            return int(v) >= UINT256_MAX
    return False


def _walk(node):
    """Every scalar under node."""
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
    else:
        yield node


class _Ctx:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")


def venue_errors(doc) -> list[str]:
    """Every rule venue.yaml breaks, as `<where>: <what>` strings; [] when it passes."""
    c = _Ctx()
    if not isinstance(doc, dict):
        return ["venue.yaml: must be a mapping (venue, origin, chains, moneyBy, login, allow, actions)"]

    _check_top(doc, c)
    allow = doc.get("allow")
    if isinstance(allow, dict):
        ids = _check_allow(allow, doc, c)
    else:
        c.err("allow", "required: the allow-list (calls, reads, signatures, sources)")
        ids = {"free": set(), "money": set(), "reads": set(), "signatures": set()}
    actions = doc.get("actions")
    money_actions: set[str] = set()
    if not isinstance(actions, dict) or not actions:
        c.err("actions", "required: at least one action")
        actions = {}
    for name, action in actions.items():
        if _check_action(str(name), action, ids, c):
            money_actions.add(str(name))
    _check_canary(doc.get("canary"), actions, money_actions, ids, c)
    return c.errors


# --- the top -----------------------------------------------------------------------------


def _check_top(doc: dict, c: _Ctx) -> None:
    venue = doc.get("venue")
    if not isinstance(venue, str) or not SLUG_RE.match(venue):
        c.err("venue", "required: the site's name, lowercase letters, digits and '-'")
    if not _is_https_origin(doc.get("origin")):
        c.err("origin", "required: the site's https origin, with no path (e.g. https://app.acme.example)")
    chains = doc.get("chains")
    if not isinstance(chains, list) or not chains or not all(
        isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in chains
    ):
        c.err("chains", "required: a list of chain ids (e.g. [8453])")
    if doc.get("moneyBy") not in MONEY_BY:
        c.err("moneyBy", f"required: one of {', '.join(MONEY_BY)} (guide Part 4, question 2)")
    if doc.get("login") not in LOGIN:
        c.err("login", f"required: one of {', '.join(LOGIN)}")
    if "keepLogin" in doc and not isinstance(doc["keepLogin"], bool):
        c.err("keepLogin", "must be true or false")


# --- the allow-list ----------------------------------------------------------------------


def _check_allow(allow: dict, doc: dict, c: _Ctx) -> dict[str, set[str]]:
    ids = {"free": set(), "money": set(), "reads": set(), "signatures": set()}
    seen: set[str] = set()

    def new_id(where: str, entry) -> str | None:
        i = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(i, str) or not SLUG_RE.match(i):
            c.err(where, "every entry needs an `id`: lowercase letters, digits and '-'")
            return None
        if i in seen:
            c.err(where, f"id {i!r} is used twice; ids are unique across calls, reads and signatures")
            return None
        seen.add(i)
        return i

    prefix = allow.get("apiPrefix")
    if not isinstance(prefix, str) or not prefix.startswith("/"):
        c.err("allow.apiPrefix", "required: the path every API call starts with (e.g. /api/)")
        prefix = "/"
    origins = allow.get("apiOrigins", [])
    if not isinstance(origins, list) or not all(_is_https_origin(o) for o in origins):
        c.err("allow.apiOrigins", "must be a list of https origins (S3: every origin the API uses)")
        origins = []
    allowed_origins = {str(o).rstrip("/") for o in origins}

    def check_origin(where: str, i: str, entry: dict) -> bool:
        """An entry on another API host names it (`origin`), and that host is in
        apiOrigins. True when the entry is on the venue's own origin."""
        o = entry.get("origin")
        if o is None:
            return True
        if not isinstance(o, str) or o.rstrip("/") not in allowed_origins:
            c.err(where, f"{i}: origin {o!r} is not in allow.apiOrigins")
        return False

    for n, call in enumerate(_list(allow, "calls", c)):
        where = f"allow.calls[{n}]"
        i = new_id(where, call)
        if i is None:
            continue
        if call.get("method") not in CALL_METHODS:
            c.err(where, f"{i}: method must be one of {', '.join(CALL_METHODS)} (a GET goes in allow.reads)")
        path = call.get("path")
        own = check_origin(where, i, call)
        if not isinstance(path, str) or not path.startswith(prefix if own else "/"):
            c.err(where, f"{i}: path must start with " + (f"apiPrefix {prefix!r}" if own else "'/'"))
        kind = call.get("kind")
        if kind not in CALL_KINDS:
            c.err(where, f"{i}: kind must be free or money")
        else:
            ids[kind].add(i)
        if "record" in call and not isinstance(call["record"], bool):
            c.err(where, f"{i}: record must be true or false")

    for n, read in enumerate(_list(allow, "reads", c)):
        where = f"allow.reads[{n}]"
        i = new_id(where, read)
        if i is None:
            continue
        if not isinstance(read.get("path"), str) or not read["path"].startswith("/"):
            c.err(where, f"{i}: path must start with '/'")
        check_origin(where, i, read)
        ids["reads"].add(i)

    sources = allow.get("sources", {})
    if not isinstance(sources, dict):
        c.err("allow.sources", "must map each address to the https link it came from")
        sources = {}
    for addr, link in sources.items():
        if not isinstance(link, str) or not link.startswith("https://"):
            c.err("allow.sources", f"{addr}: the source must be an https link (the site's docs or a block explorer)")
    known = {str(a).lower() for a in sources}

    for n, sig in enumerate(_list(allow, "signatures", c)):
        where = f"allow.signatures[{n}]"
        i = new_id(where, sig)
        if i is None:
            continue
        ids["signatures"].add(i)
        _check_signature(where, i, sig, doc, known, c)
    return ids


def _list(allow: dict, key: str, c: _Ctx) -> list[dict]:
    value = allow.get(key, [])
    if not isinstance(value, list):
        c.err(f"allow.{key}", "must be a list")
        return []
    out = []
    for n, v in enumerate(value):
        if isinstance(v, dict):
            out.append(v)
        else:
            c.err(f"allow.{key}[{n}]", "must be a mapping")
    return out


def _check_signature(where: str, i: str, sig: dict, doc: dict, known: set[str], c: _Ctx) -> None:
    kind = sig.get("kind")
    if kind not in SIGNATURE_KINDS:
        c.err(where, f"{i}: kind must be one of {', '.join(SIGNATURE_KINDS)}")
        return
    if sig.get("grantsKey") is True and doc.get("moneyBy") != "session":
        # S6: after one card, the page trades with its own key and the wallet is never
        # asked again, so only the money hold can see the trades.
        c.err(where, f"{i}: a signature that grants the page a key forces moneyBy: session (S6)")
    if "grantsKey" in sig and not isinstance(sig["grantsKey"], bool):
        c.err(where, f"{i}: grantsKey must be true or false")
    addresses: list[tuple[str, str]] = []
    if kind == "eip712":
        if not isinstance(sig.get("primaryType"), str):
            c.err(where, f"{i}: primaryType is required for eip712")
        domain = sig.get("domain")
        if not isinstance(domain, dict):
            c.err(where, f"{i}: domain is required for eip712")
        else:
            chains = doc.get("chains") if isinstance(doc.get("chains"), list) else []
            if domain.get("chainId") not in chains:
                c.err(where, f"{i}: domain.chainId must be one of the venue's chains")
            vc = domain.get("verifyingContract")
            if not isinstance(vc, str) or not ADDRESS_RE.match(vc):
                c.err(where, f"{i}: domain.verifyingContract must be a 0x address")
            else:
                addresses.append(("verifyingContract", vc))
        if str(sig.get("primaryType", "")).lower() == "setapprovalforall":
            c.err(where, f"{i}: setApprovalForAll is never allowed in a venue file")
    if kind == "transaction":
        to = sig.get("to")
        if not isinstance(to, str) or not ADDRESS_RE.match(to):
            c.err(where, f"{i}: `to` must be the 0x contract the transaction is sent to")
        else:
            addresses.append(("to", to))
        if not isinstance(sig.get("function"), str):
            c.err(where, f"{i}: `function` (the contract function's name) is required for a transaction")
        elif sig["function"].lower() == "setapprovalforall":
            c.err(where, f"{i}: setApprovalForAll is never allowed in a venue file")
    fields = sig.get("fields", {})
    if not isinstance(fields, dict):
        c.err(where, f"{i}: fields must map each field to its rule")
        fields = {}
    for fname, rule in fields.items():
        fw = f"{where}.fields.{fname}"
        if not isinstance(rule, dict):
            c.err(fw, "a field rule is a mapping: {equals: ...} or {card: <arg>, rule: at-most} ...")
            continue
        if "rule" in rule and rule["rule"] not in FIELD_RULES:
            c.err(fw, f"rule must be one of {', '.join(FIELD_RULES)}")
        if rule.get("rule") == "at-most" and "card" not in rule:
            c.err(fw, "an at-most rule must be tied to the card (`card: <arg>`)")
        for v in _walk(rule):
            if _is_unlimited(v):
                c.err(fw, "an unlimited amount is never allowed")
        eq = rule.get("equals")
        if isinstance(eq, str) and ADDRESS_RE.match(eq):
            addresses.append((f"fields.{fname}", eq))
    for what, addr in addresses:
        if addr.lower() not in known:
            c.err(where, f"{i}: {what} {addr} has no entry in allow.sources (where it came from)")


# --- the actions -------------------------------------------------------------------------


def _check_action(name: str, action, ids: dict[str, set[str]], c: _Ctx) -> bool:
    """Checks one action; True when it can move money (a send-once or click-once step)."""
    where = f"actions.{name}"
    if not ACTION_RE.match(name):
        c.err(where, "an action name is lowercase letters, digits and '-'")
    if not isinstance(action, dict):
        c.err(where, "must be a mapping with `steps`")
        return False

    args = action.get("args", {})
    if not isinstance(args, dict):
        c.err(f"{where}.args", "must map each arg to its type")
        args = {}
    for aname, spec in args.items():
        aw = f"{where}.args.{aname}"
        if not isinstance(spec, dict):
            c.err(aw, "must be a mapping with a `type`")
            continue
        t = spec.get("type")
        if t == "address":
            c.err(aw, "an action may never take an address: money leaves only to the owner's Butler wallet (S4)")
        elif t not in ARG_TYPES:
            c.err(aw, f"type must be one of {', '.join(ARG_TYPES)}; no free text (S7)")
        if t == "usd":
            mx = spec.get("max")
            if not isinstance(mx, (int, float)) or isinstance(mx, bool) or mx <= 0:
                c.err(aw, "a usd arg needs a `max` (the most one card can ever cover)")
        if t == "enum" and not (isinstance(spec.get("values"), list) and spec["values"]) and "from" not in spec:
            c.err(aw, "an enum arg needs `values` or `from`")

    steps = action.get("steps")
    if not isinstance(steps, list) or not steps:
        c.err(f"{where}.steps", "required: at least one step")
        steps = []
    money_calls_sent: set[str] = set()
    is_money = False
    for n, step in enumerate(steps):
        sw = f"{where}.steps[{n}]"
        if not isinstance(step, dict) or len(step) != 1:
            c.err(sw, f"a step is one of: {', '.join(STEP_TYPES)}")
            continue
        (kind, body), = step.items()
        if kind not in STEP_TYPES:
            c.err(sw, f"unknown step {kind!r}; one of: {', '.join(STEP_TYPES)}")
            continue
        if kind in MONEY_STEPS:
            is_money = True
        if kind == "call":
            if not isinstance(body, dict) or ("id" in body) == ("read" in body):
                c.err(sw, "a call names one `id` (an allow.calls entry) or one `read` (an allow.reads entry)")
            elif "read" in body and body["read"] not in ids["reads"]:
                c.err(sw, f"read {body['read']!r} is not in allow.reads")
            elif "id" in body and body["id"] in ids["money"]:
                c.err(sw, f"{body['id']!r} is a money call: send it with send-once, never call")
            elif "id" in body and body["id"] not in ids["free"]:
                c.err(sw, f"call {body['id']!r} is not in allow.calls")
        elif kind == "send-once":
            if not isinstance(body, dict) or body.get("id") not in ids["money"]:
                c.err(sw, "send-once names an allow.calls entry of kind money")
            else:
                money_calls_sent.add(body["id"])
        elif kind == "goto":
            path = body if isinstance(body, str) else None
            if path is None or not path.startswith("/") or "//" in path:
                c.err(sw, "goto opens a path on the venue's own origin (e.g. /portfolio), never another site")
        elif kind == "confirm":
            if not isinstance(body, dict) or body.get("read") not in ids["reads"]:
                c.err(sw, "confirm reads an allow.reads entry: the result comes from the API or chain, never page words")

    money = action.get("money", {})
    if not isinstance(money, dict):
        c.err(f"{where}.money", "must map each money call to its rules")
        money = {}
    for mid in money:
        if mid not in ids["money"]:
            c.err(f"{where}.money.{mid}", "is not an allow.calls entry of kind money")
    for mid in sorted(money_calls_sent - set(money)):
        # S2: what the card promises is enforced on the held request, not by a step.
        c.err(f"{where}.money", f"send-once {mid!r} has no rules here: tie its body to the card (S2)")
    sigs = action.get("signatures", [])
    if not isinstance(sigs, list):
        c.err(f"{where}.signatures", "must be a list of allow.signatures ids")
        sigs = []
    for s in sigs:
        if s not in ids["signatures"]:
            c.err(f"{where}.signatures", f"{s!r} is not in allow.signatures")
    if any(isinstance(s, dict) and "click-once" in s for s in steps) and not (sigs or money):
        c.err(where, "a click-once action must say what the click may cause: `signatures` or `money`")

    if is_money:
        card = action.get("card")
        if not isinstance(card, dict) or not isinstance(card.get("title"), str) or not card["title"].strip():
            c.err(f"{where}.card", "a money action needs a card with a title (the server writes it, never Butler)")
        if action.get("duty", False) is not False:
            c.err(f"{where}.duty", "venue actions are chat-only in v1: duty must be false (S9)")
    card = action.get("card")
    if isinstance(card, dict):
        texts = [card.get("title")]
        facts = card.get("facts", [])
        if isinstance(facts, list):
            texts.extend(x for f in facts if isinstance(f, list) for x in f)
        for t in texts:
            if not isinstance(t, str):
                continue
            for ph in PLACEHOLDER_RE.findall(t):
                if ph.split(".")[0] not in args:
                    # S7: card text comes only from typed args, never page text.
                    c.err(f"{where}.card", f"{{{ph}}} is not one of this action's args (S7)")
    returns = action.get("returns", [])
    if not isinstance(returns, list) or not all(isinstance(r, str) for r in returns):
        c.err(f"{where}.returns", "must be a list of field names (S11)")
    return is_money


# --- the daily check ---------------------------------------------------------------------


def _check_canary(canary, actions: dict, money_actions: set[str], ids: dict[str, set[str]], c: _Ctx) -> None:
    """The `canary` block (guide Step 7): the steps the daily check runs, each money
    step stopped at its money. It runs only what the allow-list already allows."""
    if canary is None:
        return
    if not isinstance(canary, list):
        c.err("canary", "must be a list of steps: {action, args, stopAt}")
        return
    stoppable = ids["money"] | ids["signatures"]
    for n, step in enumerate(canary):
        where = f"canary[{n}]"
        if not isinstance(step, dict):
            c.err(where, "must be a mapping: {action, args, stopAt}")
            continue
        name = step.get("action")
        if name not in actions:
            c.err(where, f"action {name!r} is not in this file's actions")
            continue
        if name == "login" and n != 0:
            c.err(where, "login runs first")
        stop = step.get("stopAt", [])
        if not isinstance(stop, list):
            c.err(where, "stopAt must be a list of allow-list ids")
            stop = []
        if name in money_actions and not stop:
            c.err(where, f"{name} can move money: it needs a stopAt, or the daily check would really send it")
        if name not in money_actions and stop:
            c.err(where, f"{name} moves no money: it has nothing to stop at")
        for s in stop:
            if s not in stoppable:
                c.err(where, f"stopAt {s!r} is not a money call or a signature in allow")
        declared = actions[name].get("args", {}) if isinstance(actions[name], dict) else {}
        declared = declared if isinstance(declared, dict) else {}
        args = step.get("args", {})
        if not isinstance(args, dict):
            c.err(where, "args must be a mapping")
            continue
        for aname, value in args.items():
            if aname not in declared:
                c.err(where, f"arg {aname!r} is not one of {name}'s args")
            if isinstance(value, dict):
                if set(value) - {"pick", "where", "field"} or value.get("pick") not in ids["reads"]:
                    c.err(where, f"arg {aname!r}: a picked arg is {{pick: <an allow.reads id>, where: ..., field: ...}}")
        for aname, spec in declared.items():
            if isinstance(spec, dict) and spec.get("required") and aname not in args:
                c.err(where, f"arg {aname!r} is required by {name}")
