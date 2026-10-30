"""test_venue.py — scripts/venue.py, the checks for a site skill's venue.yaml.

The fixture tests/fixtures/skills/site-venue holds the guide's Acme Perps example
(docs/site-skills/butler-site-skill-guide.md, Part 7) and passes. Each test below
breaks one rule in a copy of it; every one of them is a rule that holds the money,
so each must be an error.
"""
from __future__ import annotations

import copy
import importlib.util
import shutil
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "tests" / "fixtures" / "skills" / "site-venue"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


venue = _load("butler_skills_venue_t", "venue.py")
validate = _load("butler_skills_validate_t", "validate.py")
GOOD = yaml.safe_load((SITE / "venue.yaml").read_text())


def errors_after(change) -> list[str]:
    doc = copy.deepcopy(GOOD)
    change(doc)
    return venue.venue_errors(doc)


def has(errors: list[str], *needles: str) -> bool:
    return any(all(n in e for n in needles) for e in errors)


def test_the_guide_example_passes():
    assert venue.venue_errors(GOOD) == []


def test_a_skill_with_the_example_venue_validates(tmp_path):
    d = tmp_path / "site-venue"
    shutil.copytree(SITE, d)
    ok, result = validate.validate_skill(d, set(), json_mode=True, standalone=True)
    assert ok, result["errors"]


def test_a_venue_test_file_with_no_venue_file_is_an_error(tmp_path):
    d = tmp_path / "site-venue"
    shutil.copytree(SITE, d)
    (d / "venue.yaml").unlink()
    ok, result = validate.validate_skill(d, set(), json_mode=True, standalone=True)
    assert not ok and has(result["errors"], "venue.test.yaml", "no venue.yaml")


def test_broken_yaml_is_an_error(tmp_path):
    d = tmp_path / "site-venue"
    shutil.copytree(SITE, d)
    (d / "venue.yaml").write_text("venue: [unclosed\n")
    ok, result = validate.validate_skill(d, set(), json_mode=True, standalone=True)
    assert not ok and has(result["errors"], "venue.yaml", "not valid YAML")


@pytest.mark.parametrize("raw", ["raw/acme.har", "trace.zip", "record/test-wallet.key", "screens/open-1-01.png", "recording.json"])
def test_raw_recording_files_are_never_committed(tmp_path, raw):
    """S8: the HAR, trace, key and screens can hold cookies, tokens or the test key."""
    d = tmp_path / "site-venue"
    shutil.copytree(SITE, d)
    (d / raw).parent.mkdir(parents=True, exist_ok=True)
    (d / raw).write_text("x")
    ok, result = validate.validate_skill(d, set(), json_mode=True, standalone=True)
    assert not ok and has(result["errors"], raw, "raw recording file")


def test_a_credential_in_the_recording_is_an_error(tmp_path):
    d = tmp_path / "site-venue"
    shutil.copytree(SITE, d)
    with (d / "venue.test.yaml").open("a") as f:
        f.write("# sk-" + "a" * 40 + "\n")
    ok, result = validate.validate_skill(d, set(), json_mode=True, standalone=True)
    assert not ok and has(result["errors"], "venue.test.yaml", "credential")


# --- the allow-list ------------------------------------------------------------------------


def test_an_unlimited_approve_is_refused():
    def change(d):
        d["allow"]["signatures"][1]["fields"]["value"] = {"equals": str(2**256 - 1)}
    assert has(errors_after(change), "fields.value", "unlimited")


def test_a_contract_with_no_source_is_refused():
    def change(d):
        del d["allow"]["sources"]["0x1111111111111111111111111111111111111111"]
    assert has(errors_after(change), "deposit-permit", "0x1111111111111111111111111111111111111111", "allow.sources")


def test_set_approval_for_all_is_never_allowed():
    def change(d):
        d["allow"]["signatures"].append({
            "id": "nft", "kind": "transaction", "function": "setApprovalForAll",
            "to": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        })
    assert has(errors_after(change), "nft", "setApprovalForAll")


def test_a_key_granting_signature_forces_a_session_site():
    """S6: after one card the page trades with its own key; only the money hold sees it."""
    def change(d):
        d["moneyBy"] = "wallet"
        d["allow"]["signatures"][0]["grantsKey"] = True
    assert has(errors_after(change), "login", "moneyBy: session", "S6")


def test_api_origins_must_be_https():
    def change(d):
        d["allow"]["apiOrigins"] = ["http://api.acme.example"]
    assert has(errors_after(change), "allow.apiOrigins")


def test_ids_are_unique_across_the_allow_list():
    def change(d):
        d["allow"]["reads"].append({"id": "order", "path": "/api/x"})
    assert has(errors_after(change), "'order' is used twice")


def test_a_call_outside_the_api_prefix_is_refused():
    def change(d):
        d["allow"]["calls"][2]["path"] = "/internal/orders"
    assert has(errors_after(change), "order", "apiPrefix")


# --- the actions ---------------------------------------------------------------------------


def test_an_action_never_takes_an_address():
    """S4: money leaves only to the owner's own Butler wallet."""
    def change(d):
        d["actions"]["withdraw"]["args"]["to"] = {"type": "address"}
    assert has(errors_after(change), "withdraw.args.to", "S4")


def test_free_text_args_are_refused():
    """S7: card text comes only from typed args."""
    def change(d):
        d["actions"]["open"]["args"]["note"] = {"type": "string"}
    assert has(errors_after(change), "open.args.note", "S7")


def test_a_usd_arg_needs_a_max():
    def change(d):
        del d["actions"]["open"]["args"]["usd"]["max"]
    assert has(errors_after(change), "open.args.usd", "max")


def test_a_money_call_must_be_tied_to_the_card():
    """S2: the card's promise is checked on the held request, not by a step."""
    def change(d):
        del d["actions"]["open"]["money"]
    assert has(errors_after(change), "open.money", "'order'", "S2")


def test_a_money_call_is_never_sent_with_a_plain_call():
    def change(d):
        d["actions"]["open"]["steps"][4] = {"call": {"id": "order"}}
    assert has(errors_after(change), "'order' is a money call", "send-once")


def test_a_money_action_needs_a_card():
    def change(d):
        del d["actions"]["withdraw"]["card"]
    assert has(errors_after(change), "withdraw.card", "title")


def test_venue_actions_are_chat_only():
    """S9: no duty may run a venue action with no card per run, in v1."""
    def change(d):
        d["actions"]["open"]["duty"] = "allowance"
    assert has(errors_after(change), "open.duty", "S9")


def test_card_text_uses_only_the_actions_args():
    def change(d):
        d["actions"]["open"]["card"]["title"] = "Open {pageTitle}"
    assert has(errors_after(change), "open.card", "{pageTitle}", "S7")


def test_goto_stays_on_the_venues_own_origin():
    def change(d):
        d["actions"]["login"]["steps"][0] = {"goto": "https://evil.example/"}
    assert has(errors_after(change), "login.steps[0]", "own origin")


def test_a_result_is_read_never_taken_from_page_words():
    def change(d):
        d["actions"]["open"]["steps"][5] = {"confirm": {"text": "Order filled"}}
    assert has(errors_after(change), "open.steps[5]", "allow.reads")


def test_an_unknown_step_type_is_refused():
    def change(d):
        d["actions"]["positions"]["steps"].append({"eval": "fetch('/x')"})
    assert has(errors_after(change), "positions.steps[1]", "unknown step 'eval'")


# --- the daily check -----------------------------------------------------------------------


def test_a_canary_money_step_needs_a_stop():
    """Without a stopAt the daily check would really send the order."""
    def change(d):
        del d["canary"][1]["stopAt"]
    assert has(errors_after(change), "canary[1]", "needs a stopAt")


def test_a_canary_stop_names_a_money_call_or_a_signature():
    def change(d):
        d["canary"][1]["stopAt"] = ["quote"]
    assert has(errors_after(change), "canary[1]", "stopAt 'quote'")


def test_a_canary_step_names_a_real_action():
    def change(d):
        d["canary"].append({"action": "close", "stopAt": ["order"]})
    assert has(errors_after(change), "canary[3]", "'close' is not in this file's actions")


def test_a_canary_pick_reads_an_allowed_read():
    def change(d):
        d["canary"][1]["args"]["market"] = {"pick": "top-markets", "where": {"symbol": "ETH"}}
    assert has(errors_after(change), "canary[1]", "market", "allow.reads")


def test_canary_login_runs_first():
    def change(d):
        d["canary"].append(d["canary"].pop(0))
    assert has(errors_after(change), "login runs first")


def test_a_canary_step_gives_every_required_arg():
    def change(d):
        del d["canary"][1]["args"]["side"]
    assert has(errors_after(change), "canary[1]", "'side' is required")


def test_a_read_only_canary_step_has_nothing_to_stop_at():
    def change(d):
        d["canary"].insert(1, {"action": "positions", "stopAt": ["order"]})
    assert has(errors_after(change), "canary[1]", "moves no money")
