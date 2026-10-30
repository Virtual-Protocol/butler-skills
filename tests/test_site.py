"""test_site.py — scripts/site.py's scrub and merge (the part that decides what Claude
sees), and the recording wallet when eth-account is installed. No browser: Playwright
is a developer-laptop dependency (scripts/requirements-site.txt), never CI's."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("butler_skills_site", REPO_ROOT / "scripts" / "site.py")
site = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(site)

OWNER = "0xAbCdEf0123456789abcdef0123456789ABCDEF01"
ORIGIN = "https://app.acme.example"
T0 = 1_800_000_000.0


def entry(url, method="GET", body=None, status=200, answer=None, mime="application/json", rtype="fetch", at="2027-01-15T08:00:01.000Z"):
    e = {
        "startedDateTime": at,
        "_resourceType": rtype,
        "request": {"method": method, "url": url, "headers": [{"name": "Cookie", "value": "sid=abc"}]},
        "response": {"status": status, "headers": [{"name": "Set-Cookie", "value": "sid=abc"}],
                     "content": {"mimeType": mime, "text": json.dumps(answer) if answer is not None else ""}},
    }
    if body is not None:
        e["request"]["postData"] = {"text": json.dumps(body)}
    return e


def test_scrub_removes_secrets_and_the_owner_address():
    out = site.scrub({
        "accessToken": "abc", "Authorization": "Bearer x", "maker": OWNER.lower(),
        "note": "jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl here", "contact": "me@example.com",
        "key": "sk-" + "a" * 20, "sig": "0x" + "ab" * 65, "rows": [1, 2, 3, 4, 5], "long": "x" * 500,
    }, OWNER)
    assert out["accessToken"] == out["Authorization"] == out["sig"] == site.SCRUBBED
    assert out["maker"] == "0xOWNER"
    assert "eyJ" not in out["note"] and out["contact"] == "<email>"
    assert out["key"] == site.SCRUBBED
    assert out["rows"] == [1, 2, 3, "<2 more>"]
    assert len(out["long"]) == site.MAX_STRING + 1


def test_har_events_keep_only_the_sites_own_api_calls_with_no_headers():
    har = {"log": {"entries": [
        entry(f"{ORIGIN}/api/markets", answer=[{"id": "m-eth", "symbol": "ETH"}]),
        entry(f"{ORIGIN}/api/orders", "POST", body={"usd": "5", "maker": OWNER}, answer={"id": "o-1"}),
        entry(f"{ORIGIN}/logo.png", mime="image/png", rtype="image"),
        entry(f"{ORIGIN}/app.js", mime="application/javascript", rtype="script"),
        entry("https://analytics.example/collect", "POST", body={"e": 1}),
        entry(f"{ORIGIN}/api/me?session=abc&usd=5", answer={"ok": True}),
    ]}}
    started = site._parse_time("2027-01-15T08:00:00.000Z")
    events = site.har_events(har, {ORIGIN}, started, OWNER)
    assert [(e["request"]["method"], e["request"]["path"]) for e in events] == [
        ("GET", "/api/markets"), ("POST", "/api/orders"), ("GET", f"/api/me?session={site.SCRUBBED}&usd=5"),
    ]
    assert events[1]["request"]["body"] == {"usd": "5", "maker": "0xOWNER"}
    assert events[0]["t"] == 1.0
    assert "sid=abc" not in json.dumps(events)


def test_a_wallet_call_is_decoded_and_scrubbed():
    typed = {"domain": {"verifyingContract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "chainId": 8453},
             "primaryType": "Permit", "message": {"owner": OWNER, "value": "2000000"}}
    ev = site.wallet_event({"t": T0 + 2.3, "method": "eth_signTypedData_v4", "params": [OWNER, json.dumps(typed)],
                            "chainId": 8453, "result": "0x" + "cd" * 65}, T0, OWNER)
    assert ev == {"t": 2.3, "wallet": {"method": "eth_signTypedData_v4", "chainId": 8453,
                                       "verifyingContract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                                       "primaryType": "Permit", "message": {"owner": "0xOWNER", "value": "2000000"}},
                  "answer": "0x<signature>"}


def test_a_run_merges_into_one_timeline_in_time_order():
    run = {"action": "withdraw", "run": 2, "usd": 3, "started": T0,
           "har": {"log": {"entries": [entry(f"{ORIGIN}/api/withdraw", "POST", body={"usd": "3"}, at="1970-01-01T00:00:00Z")]}},
           "wallet": [{"t": T0 + 1.0, "method": "eth_accounts", "params": [], "chainId": 8453, "result": [OWNER]}],
           "clicks": [{"t": T0 + 0.5, "role": "button", "name": "Withdraw"}],
           "sockets": [{"t": T0 + 0.7, "url": "wss://app.acme.example/ws", "data": '{"op":"sub"}'}]}
    run["har"]["log"]["entries"][0]["startedDateTime"] = None  # t falls back to 0
    out = site.merge_run(run, {ORIGIN}, OWNER)
    assert out["action"] == "withdraw" and out["usd"] == "3"
    kinds = [next(k for k in ev if k != "t") for ev in out["timeline"]]
    assert kinds == ["request", "click", "socketSend", "wallet"]
    assert out["timeline"][3]["answer"] == ["0xOWNER"]


def test_the_recording_is_written_from_raw(tmp_path):
    work = tmp_path / "acme-perps"
    (work / "raw").mkdir(parents=True)
    conf = {"site": "acme-perps", "origin": ORIGIN, "address": OWNER, "actions": ["open"]}
    (work / "raw" / "open-1.meta.json").write_text(json.dumps({"action": "open", "run": 1, "usd": None, "started": T0}))
    (work / "raw" / "open-1.har").write_text(json.dumps({"log": {"entries": [entry(f"{ORIGIN}/api/markets", answer=[])]}}))
    out = site.write_recording(work, conf)
    rec = json.loads(out.read_text())
    assert rec["site"] == "acme-perps" and [a["action"] for a in rec["actions"]] == ["open"]


def test_try_calls_compares_status_and_shape():
    assert site.compare_answers({"status": 200, "body": {"a": 1, "b": 2}}, {"status": 200, "body": {"b": 3, "a": 0}})["callWorks"]
    assert not site.compare_answers({"status": 200, "body": {"a": 1}}, {"status": 401, "body": {"a": 1}})["callWorks"]
    assert not site.compare_answers({"status": 200, "body": [{"a": 1}]}, {"status": 200, "body": {"error": "x"}})["callWorks"]


def test_actions_must_be_plain_names():
    assert site._actions("login, open,withdraw") == ["login", "open", "withdraw"]
    with pytest.raises(SystemExit):
        site._actions("open;rm -rf")


def test_the_recording_wallet_signs_and_logs_and_never_sends_without_rpc():
    pytest.importorskip("eth_account")
    from eth_account import Account
    from eth_account.messages import encode_defunct

    key = "0x" + "11" * 32
    w = site.RecordingWallet(key, 8453, rpc=None)
    assert json.loads(w.handle(json.dumps({"method": "eth_requestAccounts"})))["result"] == [w.address]
    sig = json.loads(w.handle(json.dumps({"method": "personal_sign", "params": ["0x" + b"hello".hex(), w.address]})))["result"]
    assert Account.recover_message(encode_defunct(text="hello"), signature=sig) == w.address
    sent = json.loads(w.handle(json.dumps({"method": "eth_sendTransaction", "params": [{"to": OWNER, "value": "0x1"}]})))
    assert sent["error"]["code"] == 4200 and "nothing was sent" in sent["error"]["message"]
    blind = json.loads(w.handle(json.dumps({"method": "eth_sign", "params": [w.address, "0x" + "00" * 32]})))
    assert blind["error"]["code"] == 4200
    assert [e["method"] for e in w.log] == ["eth_requestAccounts", "personal_sign", "eth_sendTransaction", "eth_sign"]
    json.loads(w.handle(json.dumps({"method": "wallet_switchEthereumChain", "params": [{"chainId": "0x89"}]})))
    assert w.chain_id == 137


def test_a_sign_in_message_is_kept_whole():
    msg = "polymarket.com wants you to sign in with your Ethereum account:\n" + OWNER + "\n\n" + "URI: https://polymarket.com\nVersion: 1\nChain ID: 137\nNonce: abc\nIssued At: 2026-10-30T04:00:00Z" + "\n" * 1 + "x" * 150
    ev = site.wallet_event({"t": T0, "method": "personal_sign", "params": ["0x" + msg.encode().hex(), OWNER], "chainId": 137, "result": "0x" + "ab" * 65}, T0, OWNER)
    assert ev["wallet"]["message"].endswith("x" * 150) and "0xOWNER" in ev["wallet"]["message"]


def test_prefetches_analytics_and_other_sites_are_left_out_but_listed():
    har = {"log": {"entries": [
        entry(f"{ORIGIN}/event/x?_rsc=abc"),
        entry(f"{ORIGIN}/_a/batch", "POST", body={"e": 1}),
        entry(f"{ORIGIN}/api/_e/v1/track", "POST", body={"e": 1}),
        entry("https://gamma-api.acme.example/v1/login", "POST", body={"ok": 1}, answer={"ok": True}),
        entry("https://rpc.other.example/", "POST", body={"jsonrpc": "2.0"}),
    ]}}
    events = site.har_events(har, {ORIGIN}, 0.0, OWNER, ORIGIN)
    assert [(e["request"].get("origin"), e["request"]["path"]) for e in events] == [("https://gamma-api.acme.example", "/v1/login")]
    assert site.other_origins(har, {ORIGIN}, ORIGIN) == {"https://rpc.other.example": {"requests": 1, "writes": 1}}


def test_the_accounts_own_site_addresses_and_ids_are_hidden_but_contracts_are_not():
    proxy = "0x" + "b2" * 20
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    found: dict = {}
    site.account_values({"proxy_wallet": proxy, "userID": 10082923, "token": {"address": usdc}}, {usdc.lower()}, found)
    assert list(found) == [proxy, "10082923"]
    names = site.aliases(OWNER, list(found))
    out = site.scrub({"path": f"/v2/positions?user={proxy}", "userID": 10082923, "spender": usdc, "maker": OWNER}, names)
    assert out == {"path": "/v2/positions?user=0xACCOUNT1", "userID": "<account-id-1>", "spender": usdc, "maker": "0xOWNER"}
    assert site.unalias("/v2/positions?user=0xACCOUNT1&by=0xOWNER", [[proxy, "0xACCOUNT1"], [OWNER, "0xOWNER"]]) == f"/v2/positions?user={proxy}&by={OWNER}"
