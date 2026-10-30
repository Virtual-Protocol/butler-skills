#!/usr/bin/env python3
"""site.py — record a website's actions for a site skill (docs/site-skills, Part 5, Step 1).

Developer laptop only. The engine on bevo-server never runs this: it drives its own
browser. This file records what a site does, so Claude can draft venue.yaml and
SKILL.md from what really happened instead of a guess.

    pip install -r scripts/requirements-site.txt && playwright install chromium

    # 1a. Record: headed Chromium + the Playwright Inspector, one recording per action
    python3 scripts/site.py record acme-perps --origin https://app.acme.example \\
        --repo ../butler-skill-acme-perps --actions login,deposit,open,withdraw

    # 1b. Replay record/<action>.py with other small amounts (no hand clicking)
    python3 scripts/site.py replay acme-perps --actions open,withdraw --usd 2,3

    # 1c. Send each recorded read again as a call from inside the page; compare
    python3 scripts/site.py try-calls acme-perps

    # Rebuild recording.json from raw/ (each command above already does)
    python3 scripts/site.py scrub acme-perps

Everything lands in one work folder, outside every repo (default
~/.cache/butler-site/<site>, or --work):

    raw/                 NEVER committed: HAR, trace, wallet log per run (cookies, tokens)
    profile/             the browser profile, so the login is kept between runs
    test-wallet.key      the TEST key the recording wallet signs with (a few dollars only)
    recording.json       the scrubbed timeline Claude reads
    call-vs-click.json   try-calls' answer, per recorded read
    site.json            origin, repo and actions, so later commands need only <site>

record/<action>.py goes into the skill repo (--repo): one Playwright script per action,
re-run when the site changes. Delete raw/ after the review (guide Step 4).

The recording wallet: an EIP-1193 + EIP-6963 wallet injected into every page, backed
by the test key. It logs every request and signs it, so a replay never waits for a
popup. Never give it a real owner's key or a team wallet's key.

SITE_BROWSER_CHANNEL=chrome uses your installed Chrome instead of Playwright's Chromium.

Safety: try-calls sends only GETs unless you name a write with --write. A write can
move money; name one only when you know it does not.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

DEFAULT_WORK = Path.home() / ".cache" / "butler-site"
SITE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ACTION_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

# --- scrub + merge (pure; tests/test_site.py) ----------------------------------------------

# Request kinds the venue file never needs: they only make the recording big.
DROP_RESOURCE_TYPES = {"image", "font", "stylesheet", "media", "manifest", "other", "texttrack"}
DROP_MIME_RE = re.compile(r"^(image|font|audio|video)/|text/css|javascript")
SECRET_KEY_RE = re.compile(
    r"(token|auth|cookie|session|secret|passw|jwt|api[-_]?key|private|mnemonic|seed|signature|^sig$|email|phone|otp)",
    re.I,
)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
LONG_SECRET_RE = re.compile(r"\b(?:sk-|brt_|ghp_|xox[bp]-)[A-Za-z0-9_-]{8,}|\b[0-9a-fA-F]{64}\b")
SIG_RE = re.compile(r"^0x[0-9a-fA-F]{130,}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAX_STRING = 200
MAX_WALLET_MESSAGE = 1200  # a sign-in message is read whole: domain, URI, chain
MAX_LIST = 3
# Same-site analytics the page posts to itself (seen live on polymarket.com: /_a/batch,
# /api/_e/v1/track) and Cloudflare's bot check (/cdn-cgi/): never part of an action.
ANALYTICS_PATH_RE = re.compile(r"/_[ae]/|/(track|identify|collect|telemetry|analytics|beacon)(/|$)|^/cdn-cgi/", re.I)
SCRUBBED = "<scrubbed>"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Keys whose value is the test account's own address or id on the site (seen live on
# polymarket.com's /v1/users/me: proxy_wallet, walletAddress, payout_address, userID).
ACCOUNT_KEY_RE = re.compile(r"wallet|address|proxy|safe|deposit|funder|maker|owner|account|payout|user_?id", re.I)
ACCOUNT_ID_KEY_RE = re.compile(r"^(user_?id|account_?id|userID|accountID)$", re.I)


def aliases(owner: str | None, accounts=()) -> list[tuple[str, str]]:
    """What scrub replaces: the test wallet → 0xOWNER (and its bare hex, inside calldata),
    each account address the site gave it → 0xACCOUNT1…, each account id → <account-id-1>…"""
    out: list[tuple[str, str]] = []
    if owner:
        out += [(owner.lower(), "0xOWNER"), (owner.lower()[2:], "OWNER")]
    n_addr = n_id = 0
    for value in accounts:
        v = str(value).lower()
        if owner and v == owner.lower():
            continue
        if ADDRESS_RE.match(v):
            n_addr += 1
            out += [(v, f"0xACCOUNT{n_addr}"), (v[2:], f"ACCOUNT{n_addr}")]
        else:
            n_id += 1
            out.append((v, f"<account-id-{n_id}>"))
    return sorted(out, key=lambda kv: -len(kv[0]))


def account_values(node, contracts: set[str], found: dict) -> None:
    """Collects the test account's own addresses and ids from a site answer: an address
    under an account-ish key (never a contract the wallet signed for), and a user id."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and ADDRESS_RE.match(v) and ACCOUNT_KEY_RE.search(str(k)) and v.lower() not in contracts:
                found.setdefault(v.lower(), None)
            elif isinstance(v, (str, int)) and not isinstance(v, bool) and ACCOUNT_ID_KEY_RE.match(str(k)) and len(str(v)) >= 6:
                found.setdefault(str(v).lower(), None)
            else:
                account_values(v, contracts, found)
    elif isinstance(node, list):
        for v in node:
            account_values(v, contracts, found)


def scrub(value, owner, max_string: int = MAX_STRING):
    """A copy of value safe to show Claude: secret-named keys and token-like strings
    replaced, the test wallet → 0xOWNER and the account's own addresses and ids → an
    alias, long lists and strings cut. `owner` is an address, or aliases() output."""
    if isinstance(owner, str):
        owner = aliases(owner)
    if isinstance(value, dict):
        return {k: (SCRUBBED if SECRET_KEY_RE.search(str(k)) else scrub(v, owner, max_string)) for k, v in value.items()}
    if isinstance(value, list):
        out = [scrub(v, owner, max_string) for v in value[:MAX_LIST]]
        if len(value) > MAX_LIST:
            out.append(f"<{len(value) - MAX_LIST} more>")
        return out
    if isinstance(value, str):
        s = value
        for old, new in owner or ():
            if old in s.lower():
                s = re.sub(re.escape(old), new, s, flags=re.I)
        if SIG_RE.match(s):
            return "0x<signature>"
        s = JWT_RE.sub(SCRUBBED, s)
        s = LONG_SECRET_RE.sub(SCRUBBED, s)
        s = EMAIL_RE.sub("<email>", s)
        return s if len(s) <= max_string else s[:max_string] + "…"
    if isinstance(value, int) and not isinstance(value, bool):
        for old, new in owner or ():
            if old == str(value):
                return new  # an account id sent as a number
    return value


def _json_or_text(text: str | None):
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _scrub_path(url: str, owner: str | None) -> str:
    u = urlsplit(url)
    path = scrub(u.path, owner)
    if not u.query:
        return path
    q = [(k, SCRUBBED if SECRET_KEY_RE.search(k) else scrub(v, owner)) for k, v in parse_qsl(u.query, keep_blank_values=True)]
    return f"{path}?{urlencode(q, safe='<>/:,')}"


def _parse_time(stamp: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def site_of(host: str) -> str:
    """The site a host belongs to: its last two labels (app.acme.example → acme.example).
    Naive on purpose; a two-part suffix like co.uk needs --api-origin."""
    return ".".join(host.split(".")[-2:])


def same_site(origin: str, site_origin: str) -> bool:
    """https://gamma-api.polymarket.com is the same site as https://polymarket.com."""
    u, s = urlsplit(origin), urlsplit(site_origin)
    return u.scheme in ("https", "wss") and bool(u.hostname) and site_of(u.hostname) == site_of(s.hostname or "")


def _is_prefetch(req: dict) -> bool:
    """A page prefetch (Next.js `RSC: 1` + `Next-Router-Prefetch`, `Purpose: prefetch`):
    the router warming a link, never a step."""
    h = {x.get("name", "").lower(): str(x.get("value", "")).lower() for x in req.get("headers", [])}
    return h.get("next-router-prefetch") == "1" or "prefetch" in (h.get("purpose", "") + h.get("sec-purpose", "")) \
        or "_rsc=" in req.get("url", "")


def other_origins(har: dict, origins: set[str], site_origin: str) -> dict[str, dict]:
    """Origins the page talked to that the recording leaves out: {origin: {requests, writes}}."""
    out: dict[str, dict] = {}
    for e in har.get("log", {}).get("entries", []):
        if e.get("_resourceType") in DROP_RESOURCE_TYPES or e.get("_resourceType") == "script":
            continue
        u = urlsplit(e.get("request", {}).get("url", ""))
        o = f"{u.scheme}://{u.netloc}"
        if o in origins or same_site(o, site_origin) or u.scheme not in ("https", "http"):
            continue
        row = out.setdefault(o, {"requests": 0, "writes": 0})
        row["requests"] += 1
        row["writes"] += e["request"].get("method", "GET") not in ("GET", "HEAD")
    return out


def har_events(har: dict, origins: set[str], started: float, owner: str | None, site_origin: str | None = None) -> list[dict]:
    """The HAR's requests to the site's own origins (and its same-site API hosts) as
    timeline events: no headers at all, no images/fonts/scripts, no prefetches or
    analytics, bodies scrubbed. `t` is seconds after `started`."""
    events = []
    for e in har.get("log", {}).get("entries", []):
        req, res = e.get("request", {}), e.get("response", {})
        url = req.get("url", "")
        u = urlsplit(url)
        o = f"{u.scheme}://{u.netloc}"
        if o not in origins and not (site_origin and same_site(o, site_origin)):
            continue
        if e.get("_resourceType") in DROP_RESOURCE_TYPES or _is_prefetch(req) or ANALYTICS_PATH_RE.search(u.path):
            continue
        mime = (res.get("content") or {}).get("mimeType") or ""
        if DROP_MIME_RE.search(mime):
            continue
        request = {"method": req.get("method", "GET"), "path": _scrub_path(url, owner)}
        if site_origin and o != site_origin:
            request["origin"] = o  # a same-site API host or an --api-origin
        body = _json_or_text((req.get("postData") or {}).get("text"))
        if body is not None:
            request["body"] = scrub(body, owner)
        answer = {"status": res.get("status")}
        content = _json_or_text((res.get("content") or {}).get("text"))
        if content is not None:
            answer["body"] = scrub(content, owner)
        t = _parse_time(e["startedDateTime"]) - started if e.get("startedDateTime") else 0.0
        events.append({"t": round(t, 1), "request": request, "answer": answer})
    return events


def wallet_event(entry: dict, started: float, owner: str | None) -> dict:
    """One recording-wallet log line as a timeline event: the method, the chain, and
    for typed data the contract, type and message (decoded, scrubbed)."""
    method, params = entry.get("method"), entry.get("params") or []
    w: dict = {"method": method, "chainId": entry.get("chainId")}
    if method == "eth_signTypedData_v4" and len(params) > 1:
        typed = params[1] if isinstance(params[1], dict) else _json_or_text(params[1])
        if isinstance(typed, dict):
            w["verifyingContract"] = (typed.get("domain") or {}).get("verifyingContract")
            w["primaryType"] = typed.get("primaryType")
            w["message"] = scrub(typed.get("message"), owner, MAX_WALLET_MESSAGE)
    elif method == "eth_sendTransaction" and params and isinstance(params[0], dict):
        tx = params[0]
        w.update({"to": tx.get("to"), "value": tx.get("value"), "selector": (tx.get("data") or "")[:10] or None})
    elif method == "personal_sign" and params:
        msg = params[0]
        if isinstance(msg, str) and msg.startswith("0x"):
            try:
                msg = bytes.fromhex(msg[2:]).decode("utf-8")
            except ValueError:
                pass
        w["message"] = scrub(msg, owner, MAX_WALLET_MESSAGE)
    event = {"t": round(entry.get("t", started) - started, 1), "wallet": scrub(w, owner, MAX_WALLET_MESSAGE)}
    if "error" in entry:
        event["answer"] = {"error": scrub(entry["error"], owner)}
    else:
        event["answer"] = scrub(entry.get("result"), owner)
    return event


def merge_run(run: dict, origins: set[str], owner: str | None, site_origin: str | None = None) -> dict:
    """One action run → its recording.json entry: requests, wallet calls, clicks and
    socket sends, in time order."""
    started = run["started"]
    timeline = har_events(run.get("har") or {}, origins, started, owner, site_origin)
    timeline += [wallet_event(w, started, owner) for w in run.get("wallet", [])]
    timeline += [
        {"t": round(c["t"] - started, 1), "click": scrub({k: v for k, v in c.items() if k != "t"}, owner)}
        for c in run.get("clicks", [])
    ]
    timeline += [
        {"t": round(s["t"] - started, 1), "socketSend": {"url": _scrub_path(s["url"], owner), "data": scrub(_json_or_text(s.get("data")), owner)}}
        for s in run.get("sockets", [])
    ]
    timeline.sort(key=lambda ev: ev["t"])
    out = {"action": run["action"], "run": run["run"]}
    if run.get("usd") is not None:
        out["usd"] = str(run["usd"])
    out["timeline"] = timeline
    return out


def build_recording(site: str, origin: str, runs: list[dict], owner: str | None, extra_origins=()) -> dict:
    """recording.json from every run on disk."""
    origins = {origin.rstrip("/"), *(o.rstrip("/") for o in extra_origins)}
    # First pass: what the site says about the test account (its own wallet on the site,
    # its user id), so every later mention of it is hidden too, not just the test key's.
    contracts = set()
    for r in runs:
        for w in r.get("wallet", []):
            for prm in w.get("params") or []:
                typed = prm if isinstance(prm, dict) else (_json_or_text(prm) if isinstance(prm, str) and prm.startswith("{") else None)
                if isinstance(typed, dict):
                    for c in (typed.get("to"), (typed.get("domain") or {}).get("verifyingContract")):
                        if isinstance(c, str):
                            contracts.add(c.lower())
    found: dict = {}
    for r in runs:
        for e in (r.get("har") or {}).get("log", {}).get("entries", []):
            account_values(_json_or_text((e.get("response", {}).get("content") or {}).get("text")), contracts, found)
    owner = aliases(owner, list(found))
    others: dict[str, dict] = {}
    for r in runs:
        for o, row in other_origins(r.get("har") or {}, origins, origin).items():
            agg = others.setdefault(o, {"requests": 0, "writes": 0})
            agg["requests"] += row["requests"]
            agg["writes"] += row["writes"]
    return {
        "site": site,
        "origin": origin,
        "recorded": date.today().isoformat(),
        "note": "Scrubbed. Never commit the raw/ files this came from.",
        # Left out: other sites' hosts (wallet SDKs, RPCs, analytics). One the site's API
        # really uses goes in with `scrub <site> --api-origin <origin>`.
        "otherOrigins": dict(sorted(others.items(), key=lambda kv: -kv[1]["requests"])),
        "actions": [merge_run(r, origins, owner, origin) for r in sorted(runs, key=lambda r: (r["action"], r["run"]))],
        # Popped by write_recording into raw/aliases.json (never committed): try-calls
        # puts the real values back to call the site.
        "_aliases": owner,
    }


def compare_answers(recorded: dict, called: dict) -> dict:
    """try-calls' verdict for one read: same status, and the same top-level shape."""
    def shape(body):
        if isinstance(body, dict):
            return sorted(body)
        if isinstance(body, list):
            return ["list", shape(body[0]) if body else None]
        return type(body).__name__

    same_status = recorded.get("status") == called.get("status")
    same_shape = shape(recorded.get("body")) == shape(called.get("body"))
    return {"sameStatus": same_status, "sameShape": same_shape, "callWorks": same_status and same_shape}


# --- the work folder -----------------------------------------------------------------------


def work_dir(args) -> Path:
    return Path(args.work).expanduser().resolve() / args.site if args.work else DEFAULT_WORK / args.site


def load_site(work: Path) -> dict:
    p = work / "site.json"
    if not p.is_file():
        raise SystemExit(f"{p} not found: run `site.py record` for this site first")
    return json.loads(p.read_text())


def save_site(work: Path, conf: dict) -> None:
    (work / "site.json").write_text(json.dumps(conf, indent=2) + "\n")


def runs_on_disk(work: Path) -> list[dict]:
    """Every run raw/ holds: raw/<action>-<run>.{har,wallet.json,clicks.json,sockets.json,meta.json}."""
    runs = []
    for meta in sorted((work / "raw").glob("*.meta.json")):
        m = json.loads(meta.read_text())
        stem = meta.name[: -len(".meta.json")]
        def read(suffix, default):
            f = work / "raw" / f"{stem}{suffix}"
            return json.loads(f.read_text()) if f.is_file() else default
        runs.append({**m, "har": read(".har", {}), "wallet": read(".wallet.json", []),
                     "clicks": read(".clicks.json", []), "sockets": read(".sockets.json", [])})
    return runs


def write_recording(work: Path, conf: dict) -> Path:
    owner = conf.get("address")
    rec = build_recording(conf["site"], conf["origin"], runs_on_disk(work), owner, conf.get("apiOrigins", []))
    (work / "raw" / "aliases.json").write_text(json.dumps(rec.pop("_aliases"), indent=1) + "\n")
    out = work / "recording.json"
    out.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    size = out.stat().st_size
    print(f"wrote {out} ({size // 1024} KB, {len(rec['actions'])} run(s))")
    writers = [o for o, row in rec["otherOrigins"].items() if row["writes"]]
    if writers:
        print(f"  left out, but the page wrote to: {', '.join(writers[:6])}")
        print("  if one is the site's own API, add it: site.py scrub <site> --api-origin <origin>")
    if size > 200 * 1024:
        print("  note: over 200 KB — record fewer runs, or check for a big answer worth dropping")
    return out


# --- the browser (Playwright, eth-account: scripts/requirements-site.txt) ------------------

WALLET_JS = r"""
(() => {
  if (window.__siteWalletInstalled) return;
  window.__siteWalletInstalled = true;
  const listeners = {};
  const provider = {
    isMetaMask: false,
    isButlerTestWallet: true,
    request: async ({ method, params }) => {
      const out = JSON.parse(await window.__siteWallet(JSON.stringify({ method, params: params || [] })));
      if (out.error) { const e = new Error(out.error.message); e.code = out.error.code; throw e; }
      if (method === 'wallet_switchEthereumChain') (listeners.chainChanged || []).forEach(f => f(out.chainId));
      return out.result;
    },
    on: (ev, f) => { (listeners[ev] = listeners[ev] || []).push(f); return provider; },
    removeListener: (ev, f) => { listeners[ev] = (listeners[ev] || []).filter(g => g !== f); return provider; },
  };
  window.ethereum = provider;
  const info = { uuid: 'b0a1e7c2-5e1f-4c1d-9a3b-7e57b0717e57', name: 'Butler Test Wallet',
                 icon: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#2f5bea"/><text x="16" y="22" font-family="Arial" font-size="17" font-weight="700" fill="#fff" text-anchor="middle">B</text></svg>'), rdns: 'gg.virtuals.butler-test' };
  const announce = () => window.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail: Object.freeze({ info, provider }) }));
  window.addEventListener('eip6963:requestProvider', announce);
  announce();
  document.addEventListener('click', (e) => {
    const el = e.target && e.target.closest ? e.target.closest('button,a,[role],input,label,li') || e.target : e.target;
    if (!el || !window.__siteClick) return;
    const name = (el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'))) || (el.innerText || el.value || '').trim().slice(0, 80);
    window.__siteClick(JSON.stringify({ role: (el.getAttribute && el.getAttribute('role')) || el.tagName.toLowerCase(), name }));
  }, true);
})();
"""


class RecordingWallet:
    """Signs every request with the test key and logs it. eth_sendTransaction and chain
    reads go to --rpc; with no --rpc they are refused (code 4200), so nothing is sent."""

    def __init__(self, key_hex: str, chain_id: int, rpc: str | None):
        from eth_account import Account

        self.account = Account.from_key(key_hex)
        self.chain_id = chain_id
        self.rpc = rpc
        self.log: list[dict] = []

    @property
    def address(self) -> str:
        return self.account.address

    def _rpc(self, method: str, params: list):
        import urllib.request

        if not self.rpc:
            raise PermissionError(f"{method} needs --rpc; nothing was sent")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.rpc, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(out["error"].get("message", "rpc error"))
        return out["result"]

    def handle(self, raw: str) -> str:
        msg = json.loads(raw)
        method, params = msg["method"], msg.get("params") or []
        entry = {"t": time.time(), "method": method, "params": params, "chainId": self.chain_id}
        try:
            result = self._answer(method, params)
            entry["result"] = result
            out = {"result": result, "chainId": hex(self.chain_id)}
        except PermissionError as e:
            entry["error"] = {"code": 4200, "message": str(e)}
            out = {"error": entry["error"]}
        except Exception as e:  # the page sees a wallet error, the log keeps why
            entry["error"] = {"code": -32603, "message": str(e)[:300]}
            out = {"error": entry["error"]}
        self.log.append(entry)
        return json.dumps(out)

    def _answer(self, method: str, params: list):
        from eth_account.messages import encode_defunct

        if method in ("eth_requestAccounts", "eth_accounts"):
            return [self.address]
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "net_version":
            return str(self.chain_id)
        if method == "wallet_switchEthereumChain":
            self.chain_id = int(params[0]["chainId"], 16)
            return None
        if method in ("wallet_addEthereumChain", "wallet_watchAsset", "wallet_requestPermissions"):
            return None
        if method == "personal_sign":
            data = params[0]
            msg = encode_defunct(hexstr=data) if isinstance(data, str) and data.startswith("0x") else encode_defunct(text=data)
            return self.account.sign_message(msg).signature.to_0x_hex()
        if method == "eth_signTypedData_v4":
            typed = params[1] if isinstance(params[1], dict) else json.loads(params[1])
            return self.account.sign_typed_data(full_message=typed).signature.to_0x_hex()
        if method == "eth_sendTransaction":
            tx = dict(params[0])
            tx.pop("from", None)
            tx["chainId"] = self.chain_id
            tx.setdefault("nonce", self._rpc("eth_getTransactionCount", [self.address, "pending"]))
            tx.setdefault("gas", self._rpc("eth_estimateGas", [{**params[0], "from": self.address}]))
            if "maxFeePerGas" not in tx and "gasPrice" not in tx:
                tx["gasPrice"] = self._rpc("eth_gasPrice", [])
            tx = {k: (int(v, 16) if isinstance(v, str) and v.startswith("0x") and k not in ("to", "data") else v) for k, v in tx.items()}
            signed = self.account.sign_transaction(tx)
            return self._rpc("eth_sendRawTransaction", [signed.raw_transaction.to_0x_hex()])
        if method == "eth_sign":
            raise PermissionError("eth_sign is refused: it signs any hash, blind")
        if method.startswith("eth_"):
            return self._rpc(method, params)
        raise PermissionError(f"{method} is not supported by the recording wallet")


def load_key(work: Path, create: bool) -> str:
    p = work / "test-wallet.key"
    if not p.is_file():
        if not create:
            raise SystemExit(f"{p} not found: run `site.py record` first")
        from eth_account import Account

        p.write_text(Account.create().key.hex() + "\n")
        p.chmod(0o600)
        print(f"made a NEW test wallet key at {p} — fund it with a few dollars only")
    return p.read_text().strip()


def browser_channel() -> str | None:
    """SITE_BROWSER_CHANNEL=chrome runs your installed Chrome instead of Playwright's Chromium."""
    return os.environ.get("SITE_BROWSER_CHANNEL") or None


def run_browser(work: Path, conf: dict, action: str, run_no: int, usd, body, headless: bool = False) -> None:
    """One action run in a headed Chromium with the HAR, trace, wallet, click and socket
    logs on; `body(page)` does the action. Writes raw/<action>-<run>.*"""
    from playwright.sync_api import sync_playwright

    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    stem = f"{action}-{run_no}"
    wallet = RecordingWallet(load_key(work, create=False), conf["chainId"], conf.get("rpc"))
    clicks: list[dict] = []
    sockets: list[dict] = []
    started = time.time()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(work / "profile"),
            headless=headless,
            channel=browser_channel(),
            base_url=conf["origin"],
            record_har_path=str(raw / f"{stem}.har"),
            record_har_content="embed",
            viewport={"width": 1300, "height": 900},
        )
        ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
        ctx.expose_function("__siteWallet", wallet.handle)
        ctx.expose_function("__siteClick", lambda raw_click: clicks.append({"t": time.time(), **json.loads(raw_click)}))
        ctx.add_init_script(WALLET_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_socket(ws):
            ws.on("framesent", lambda payload: sockets.append({"t": time.time(), "url": ws.url, "data": payload if isinstance(payload, str) else "<binary>"}))

        page.on("websocket", on_socket)
        try:
            body(page)
        finally:
            ctx.tracing.stop(path=str(raw / f"{stem}.trace.zip"))
            ctx.close()  # writes the HAR
    (raw / f"{stem}.wallet.json").write_text(json.dumps(wallet.log, indent=1))
    (raw / f"{stem}.clicks.json").write_text(json.dumps(clicks, indent=1))
    (raw / f"{stem}.sockets.json").write_text(json.dumps(sockets, indent=1))
    (raw / f"{stem}.meta.json").write_text(json.dumps({"action": action, "run": run_no, "usd": usd, "started": started}))
    print(f"  {stem}: {len(wallet.log)} wallet call(s), {len(clicks)} click(s), {len(sockets)} socket send(s)")


RECORD_STUB = '''"""record/{action}.py — {site} `{action}`, recorded with butler-skills/scripts/site.py.

Paste the lines the Playwright Inspector wrote for this action into run(). Paths are
relative to the site ({origin}). `usd` is the amount for a money action, as a string.
Re-run with `site.py replay {site} --actions {action}`: when the site changes, the
script that fails shows which step broke.
"""


def run(page, usd=None):
    page.goto("/")
    # page.get_by_role("button", name="...").click()
    # page.get_by_label("Amount").fill(usd)
'''


def cmd_record(args) -> int:
    work = work_dir(args)
    work.mkdir(parents=True, exist_ok=True)
    origin = args.origin.rstrip("/")
    u = urlsplit(origin)
    if u.scheme != "https" or u.path not in ("", "/"):
        raise SystemExit("--origin must be the site's https origin, with no path")
    repo = Path(args.repo).expanduser().resolve()
    actions = _actions(args.actions)
    key = load_key(work, create=True)
    from eth_account import Account

    conf = {
        "site": args.site, "origin": origin, "repo": str(repo), "actions": actions,
        "chainId": args.chain_id, "rpc": args.rpc, "apiOrigins": args.api_origin or [],
        "address": Account.from_key(key).address,
    }
    save_site(work, conf)
    (repo / "record").mkdir(parents=True, exist_ok=True)
    print(f"test wallet: {conf['address']} (chain {args.chain_id}) — a few dollars only")
    for action in actions:
        print(f"\n== {action}: do it once in the browser. In the Inspector, press Record first;")
        print(f"   when done, copy its lines into {repo / 'record' / (action + '.py')} and press Resume.")
        stub = repo / "record" / f"{action}.py"
        if not stub.exists():
            stub.write_text(RECORD_STUB.format(action=action, site=args.site, origin=origin))

        def body(page):
            page.goto("/")
            page.pause()

        run_browser(work, conf, action, 1, None, body)
    write_recording(work, conf)
    print(f"\nnext: fill in record/*.py, then `site.py replay {args.site} --actions ... --usd 2,3`")
    return 0


def cmd_replay(args) -> int:
    import importlib.util

    work = work_dir(args)
    conf = load_site(work)
    actions = _actions(args.actions) if args.actions else conf["actions"]
    amounts = [a.strip() for a in args.usd.split(",")] if args.usd else [None]
    failed = 0
    for action in actions:
        script = Path(conf["repo"]) / "record" / f"{action}.py"
        if not script.is_file():
            print(f"  {action}: no {script}; skipped")
            failed += 1
            continue
        spec = importlib.util.spec_from_file_location(f"record_{action.replace('-', '_')}", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        done = [int(m.name.split("-")[-1].split(".")[0]) for m in (work / "raw").glob(f"{action}-*.meta.json")]
        for usd in amounts:
            run_no = max(done, default=0) + 1
            done.append(run_no)
            print(f"== replay {action} run {run_no}" + (f" (${usd})" if usd else ""))
            try:
                run_browser(work, conf, action, run_no, usd, lambda page: module.run(page, usd=usd), headless=args.headless)
                print("  passed")
            except Exception as e:  # a failed step is the answer we want: which one broke
                failed += 1
                print(f"  FAILED: {str(e).splitlines()[0][:300]}")
    write_recording(work, conf)
    return 1 if failed else 0


def unalias(text: str, pairs) -> str:
    """The real values back into a scrubbed path: 0xOWNER → the test wallet, and so on.
    Longest alias first, so 0xACCOUNT12 is never read as 0xACCOUNT1 + "2"."""
    for real, alias in sorted(pairs, key=lambda kv: -len(kv[1])):
        text = text.replace(alias, real)
    return text


def cmd_try_calls(args) -> int:
    from playwright.sync_api import sync_playwright

    work = work_dir(args)
    conf = load_site(work)
    rec = json.loads((work / "recording.json").read_text())
    aliases_file = work / "raw" / "aliases.json"
    back = json.loads(aliases_file.read_text()) if aliases_file.is_file() else []
    writes = set(args.write or [])
    seen, todo = set(), []
    for run in rec["actions"]:
        for ev in run["timeline"]:
            req = ev.get("request")
            if not req:
                continue
            key = (req["method"], req.get("origin", ""), req["path"])
            if key in seen:
                continue
            seen.add(key)
            if req.get("origin", "").startswith("wss://"):
                continue  # a websocket opening, not a call
            if req["method"] == "GET" or req["path"].split("?")[0] in writes:
                todo.append((run["action"], req, ev.get("answer") or {}))
    if any(SCRUBBED in r["path"] for _, r, _ in todo):
        print("note: some paths hold scrubbed values; those calls will differ from the recording")
    results = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(work / "profile"), headless=True, channel=browser_channel(), base_url=conf["origin"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("/")
        for action, req, answer in todo:
            # A same-site API host's request carries its origin; the page's own has none.
            path = req.get("origin", "") + unalias(req["path"], back)
            body = None if req["method"] == "GET" else json.dumps(req.get("body"))
            try:
                called = page.evaluate(
                    """async ([path, method, body]) => {
                        const go = (credentials) => fetch(path, { method, body, credentials,
                            headers: body ? { 'Content-Type': 'application/json' } : {} });
                        // With cookies first (the page's own API); a host that refuses
                        // cookies from this page (CORS) is tried without them.
                        let r; try { r = await go('include'); } catch (e) { r = await go('omit'); }
                        const text = await r.text();
                        let parsed = text; try { parsed = JSON.parse(text); } catch (e) {}
                        return { status: r.status, body: parsed };
                    }""",
                    [path, req["method"], body],
                )
            except Exception as e:  # CORS, a network error: the call does not work from the page
                called = {"status": None, "body": str(e).splitlines()[0][:200]}
            verdict = compare_answers(answer, called)
            results.append({"action": action, "method": req["method"], "origin": req.get("origin", conf["origin"]),
                            "path": req["path"], "recordedStatus": answer.get("status"),
                            "callStatus": called["status"], **verdict})
            print(f"  {'call ok ' if verdict['callWorks'] else 'differs '} {req['method']} {req.get('origin', '')}{req['path'][:90]}")
        ctx.close()
    out = work / "call-vs-click.json"
    out.write_text(json.dumps(results, indent=1) + "\n")
    print(f"wrote {out}")
    return 0


def cmd_scrub(args) -> int:
    work = work_dir(args)
    conf = load_site(work)
    if args.api_origin:
        conf["apiOrigins"] = sorted(set(conf.get("apiOrigins", [])) | {o.rstrip("/") for o in args.api_origin})
        save_site(work, conf)
    write_recording(work, conf)
    return 0


def _actions(raw: str) -> list[str]:
    actions = [a.strip() for a in raw.split(",") if a.strip()]
    bad = [a for a in actions if not ACTION_RE.match(a)]
    if not actions or bad:
        raise SystemExit(f"--actions: lowercase names, comma separated (bad: {', '.join(bad) or 'none given'})")
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a website's actions for a site skill.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("site", help="the venue's name, e.g. acme-perps")
        p.add_argument("--work", help=f"work folder root (default {DEFAULT_WORK})")

    r = sub.add_parser("record", help="1a: record each action in the Playwright Inspector")
    common(r)
    r.add_argument("--origin", required=True, help="the site's https origin")
    r.add_argument("--repo", required=True, help="the skill repo folder; record/ goes there")
    r.add_argument("--actions", required=True, help="comma-separated action names")
    r.add_argument("--chain-id", type=int, default=8453, help="the wallet's starting chain (default 8453, Base)")
    r.add_argument("--rpc", help="JSON-RPC URL for transactions and chain reads (without it they are refused)")
    r.add_argument("--api-origin", action="append", help="another origin the site's API uses (repeatable)")
    r.set_defaults(fn=cmd_record)

    p = sub.add_parser("replay", help="1b: run record/<action>.py again, per amount")
    common(p)
    p.add_argument("--actions", help="comma-separated (default: every recorded action)")
    p.add_argument("--usd", help="comma-separated amounts, one run each, e.g. 2,3")
    p.add_argument("--headless", action="store_true", help="no browser window (the record step always shows one)")
    p.set_defaults(fn=cmd_replay)

    t = sub.add_parser("try-calls", help="1c: send each recorded read as a call; write call-vs-click.json")
    common(t)
    t.add_argument("--write", action="append", help="also send this non-GET path (it may move money: only if you know it does not)")
    t.set_defaults(fn=cmd_try_calls)

    s = sub.add_parser("scrub", help="rebuild recording.json from raw/")
    common(s)
    s.add_argument("--api-origin", action="append", help="also keep this origin's requests (saved for later runs)")
    s.set_defaults(fn=cmd_scrub)

    args = parser.parse_args(argv)
    if not SITE_RE.match(args.site):
        parser.error("site: lowercase letters, digits and '-'")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
