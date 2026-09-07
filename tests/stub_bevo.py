"""stub_bevo.py — the offline `bevo` module.

A port of bevo-docker's `api/bevo_services/sdk_rehearsal.py` recording
semantics: `events()`/`trades()` replay a fixture JSONL file, the money verbs
(`trade`/`execute`/`buy`/`sell`/`long`/`short`/`close`/`stock_buy`/
`stock_sell`) record their call (including the idempotency key) into a list
instead of acting, `read()`/`rpc()` answer from fixture JSON files, `balance()`
and `is_stock()` answer from fixture/env-driven data, and `log()` just prints.
`replay.py` puts this module on `sys.path` as `bevo` so a skill's real,
unmodified `duty.py` can `import bevo` and run against captured data with no
network and no container.

Fixtures live in `fixtures/` next to this file (BEVO_STUB_FIXTURES_DIR
overrides). When BEVO_STUB_FIXTURES_URL is set (replay.py sets it to the hub's
published fixtures directory) a fixture that is missing locally is downloaded
from `<url>/<name>` on first use — the only network this stub ever touches,
and only for files that are not already on disk.

The money verbs are idempotent per `idempotency_key`, the way the real
container is: a key already used in this state dir (a `bevo_stub_ledger.json`
file written next to `duty.py`'s own `state.json`, i.e. in the replay's
working directory) answers "already executed" without a new entry in
RECORDED_ACTIONS — mirroring the real server's dedup, since a replayed skill
carries no seen-set of its own any more.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

FIXTURES_DIR = Path(os.environ.get("BEVO_STUB_FIXTURES_DIR", str(Path(__file__).parent / "fixtures")))
FIXTURE_NAME = os.environ.get("BEVO_STUB_FIXTURE", "trade-activity-page")
FIXTURES_URL = os.environ.get("BEVO_STUB_FIXTURES_URL", "").rstrip("/")

SERVICE_ID = os.environ.get("BEVO_STUB_SERVICE_ID", "stub-service-id")
SESSION_ID = os.environ.get("BEVO_STUB_SESSION_ID", "stub-session-id")

DEFAULT_STOCK_SYMBOLS = {"AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY", "QQQ"}

RECORDED_ACTIONS: list[dict] = []

LEDGER_FILE = "bevo_stub_ledger.json"


class BevoError(Exception):
    pass


def _download_fixture(name: str, dest: Path) -> bool:
    import urllib.error
    import urllib.request

    url = f"{FIXTURES_URL}/{name}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[stub_bevo] no local fixture {name} and could not download {url}: {e}")
        return False
    print(f"[stub_bevo] downloaded fixture {name} from {url}")
    return True


def _fixture_path(name: str) -> Path:
    fp = FIXTURES_DIR / name
    if not fp.exists() and FIXTURES_URL:
        _download_fixture(name, fp)
    return fp


def events():
    """Replay the fixture JSONL as a generator of event envelopes."""
    path = _fixture_path(f"{FIXTURE_NAME}.jsonl")
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


# --- trade feed: Asset / TradeEvent / trades() -------------------------------------------


class Asset:
    """A token on a chain, the way the trade feed and the money verbs name it."""

    def __init__(self, symbol=None, address=None, chain_id=None):
        self.symbol = symbol.upper() if symbol else None
        if address:
            address = str(address)
            self.address = address.lower() if address.startswith("0x") else address
        else:
            self.address = None
        self.chain_id = int(chain_id) if chain_id is not None else None

    @property
    def ref(self):
        """What a money verb should trade: the address when there is one,
        else the symbol."""
        return self.address or self.symbol

    def __str__(self):
        base = self.ref or "?"
        return f"{base}@{self.chain_id}" if self.chain_id is not None else base

    def __repr__(self):
        return f"Asset({self})"


class TradeEvent:
    def __init__(
        self,
        id=None,
        owner=None,
        wallet=None,
        owner_wallet=None,
        type=None,
        direction=None,
        token_in=None,
        token_out=None,
        token_in_address=None,
        token_out_address=None,
        amount_in=None,
        usd_value=None,
        leverage=None,
        chain_id=None,
        reduce_only=None,
        hl_event=None,
        tx_hash=None,
        command=None,
        at=None,
    ):
        self.id = id
        self.owner = owner
        self.wallet = wallet
        self.owner_wallet = owner_wallet
        self.type = type
        self.direction = direction
        self.token_in = token_in
        self.token_out = token_out
        self.token_in_address = token_in_address
        self.token_out_address = token_out_address
        self.amount_in = amount_in
        self.usd_value = usd_value
        self.leverage = leverage
        self.chain_id = chain_id
        self.reduce_only = reduce_only
        self.hl_event = hl_event
        self.tx_hash = tx_hash
        self.command = command
        self.at = at

    @property
    def is_buy(self):
        return self.direction == "buy"

    @property
    def is_sell(self):
        return self.direction == "sell"

    @property
    def is_perp(self):
        return self.type == "HL"

    @property
    def is_long(self):
        return self.is_perp and self.direction == "long"

    @property
    def is_short(self):
        return self.is_perp and self.direction == "short"

    @property
    def is_close(self):
        return self.is_perp and (self.reduce_only is True or self.hl_event in ("liquidation", "exchange_close"))

    @property
    def token(self):
        """The token the trade was about — a sell's token_in, otherwise
        token_out — symbol-or-address."""
        if self.is_sell:
            return self.token_in or self.token_in_address
        return self.token_out or self.token_out_address

    @property
    def asset(self):
        """Asset built from the sell leg for a sell, else the buy leg,
        carrying this event's chain_id."""
        if self.is_sell:
            return Asset(symbol=self.token_in, address=self.token_in_address, chain_id=self.chain_id)
        return Asset(symbol=self.token_out, address=self.token_out_address, chain_id=self.chain_id)

    def __str__(self):
        return f"TradeEvent(id={self.id} type={self.type} direction={self.direction} asset={self.asset})"


def _parse_trade_event(event: dict) -> TradeEvent:
    chain_id = event.get("chainId")
    chain_id = int(chain_id) if chain_id not in (None, "") else None
    amount_in = event.get("amountIn")
    amount_in = float(amount_in) if amount_in not in (None, "") else None
    return TradeEvent(
        id=event.get("id"),
        owner=event.get("principalId"),
        wallet=event.get("walletAddress"),
        owner_wallet=event.get("ownerWalletAddress"),
        type=event.get("type") or "SWAP",
        direction=event.get("direction"),
        token_in=event.get("tokenInSymbol"),
        token_out=event.get("tokenOutSymbol"),
        token_in_address=event.get("tokenInAddress"),
        token_out_address=event.get("tokenOutAddress"),
        amount_in=amount_in,
        usd_value=event.get("usdValue"),
        leverage=event.get("leverage"),
        chain_id=chain_id,
        reduce_only=event.get("reduceOnly"),
        hl_event=event.get("hlEvent"),
        tx_hash=event.get("txHash"),
        command=event.get("command"),
        at=event.get("at"),
    )


def trades():
    """Replay the fixture JSONL as a generator of TradeEvent, the way the
    published trade feed sends them."""
    for envelope in events():
        if envelope.get("kind") != "trade":
            continue
        yield _parse_trade_event(envelope.get("event") or {})


# --- balance() / is_stock() ---------------------------------------------------------------


class Balance:
    def __init__(self, total_usd=None, cash_usd=None, perps_usd=None):
        self.total_usd = total_usd
        self.cash_usd = cash_usd
        self.perps_usd = perps_usd

    @property
    def available(self):
        return self.total_usd is not None or self.cash_usd is not None

    def __repr__(self):
        return f"Balance(total_usd={self.total_usd}, cash_usd={self.cash_usd}, perps_usd={self.perps_usd})"


def balance() -> Balance:
    """BEVO_STUB_BALANCE_{TOTAL,CASH,PERPS}_USD env vars first (a duty can be
    replayed with a specific wallet state without a fixture file); otherwise
    fixtures/balance.json ({"totalUsd":.., "cashUsd":.., "perpsUsd":..}),
    downloaded like any other fixture when missing; otherwise unavailable."""
    total = os.environ.get("BEVO_STUB_BALANCE_TOTAL_USD")
    cash = os.environ.get("BEVO_STUB_BALANCE_CASH_USD")
    perps = os.environ.get("BEVO_STUB_BALANCE_PERPS_USD")
    if total is not None or cash is not None or perps is not None:
        return Balance(
            float(total) if total is not None else None,
            float(cash) if cash is not None else None,
            float(perps) if perps is not None else None,
        )
    fp = _fixture_path("balance.json")
    if fp.exists():
        data = json.loads(fp.read_text())
        return Balance(data.get("totalUsd"), data.get("cashUsd"), data.get("perpsUsd"))
    return Balance(None, None, None)


def _stock_symbols() -> set[str]:
    env = os.environ.get("BEVO_STUB_STOCK_SYMBOLS")
    if env is not None:
        return {s.strip().upper() for s in env.split(",") if s.strip()}
    fp = FIXTURES_DIR / "stocks.json"
    if fp.exists():
        return {str(s).upper() for s in json.loads(fp.read_text())}
    return DEFAULT_STOCK_SYMBOLS


def is_stock(symbol: str) -> bool:
    return bool(symbol) and symbol.upper() in _stock_symbols()


def log(msg: str) -> None:
    print(f"[stub_bevo.log] {msg}")


def notify(text: str) -> dict:
    action = {"call": "notify", "text": text, "key": None}
    RECORDED_ACTIONS.append(action)
    return {"status": "accepted"}


# --- idempotency ledger + money-verb recording --------------------------------------------


def _ledger_load() -> dict:
    p = Path(LEDGER_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _ledger_save(data: dict) -> None:
    try:
        Path(LEDGER_FILE).write_text(json.dumps(data))
    except OSError:
        pass


class ActionResult:
    """What a money verb hands back: `.summary` for logging, `.ok`, and
    `.asked` (the usd/pct the caller asked for, echoed back)."""

    def __init__(self, call, summary, asked=None, ok=True, already_executed=False, **extra):
        self.call = call
        self.summary = summary
        self.asked = asked
        self.ok = ok
        self.already_executed = already_executed
        for k, v in extra.items():
            setattr(self, k, v)

    def __str__(self):
        return self.summary


def _record_money_action(call: str, command: str, idempotency_key, summary: str, asked=None, **extra) -> ActionResult:
    """Append {call, command, key, ...} to RECORDED_ACTIONS unless
    idempotency_key was already used in this state dir — mirroring the real
    container's per-key dedup, since a replayed duty carries no seen-set of
    its own any more."""
    already = False
    if idempotency_key:
        ledger = _ledger_load()
        if idempotency_key in ledger:
            already = True
        else:
            ledger[idempotency_key] = call
            _ledger_save(ledger)
    if not already:
        RECORDED_ACTIONS.append({"call": call, "command": command, "key": idempotency_key, **extra})
    return ActionResult(call, summary, asked=asked, already_executed=already, **extra)


def _ref_and_chain(token) -> tuple[str, int | None]:
    if isinstance(token, Asset):
        return token.ref, token.chain_id
    return str(token), None


def trade(command=None, params=None, message=None, idempotency_key=None, max_attempts=3) -> dict:
    action = {
        "call": "trade",
        "command": command,
        "params": params,
        "message": message,
        "key": idempotency_key,
    }
    RECORDED_ACTIONS.append(action)
    return {"status": "accepted", "idempotencyKey": idempotency_key, "executionLogId": len(RECORDED_ACTIONS)}


def execute(to, data="0x", value=None, chain_id=8453, idempotency_key=None, max_attempts=3) -> dict:
    action = {
        "call": "execute",
        "to": to,
        "data": data,
        "value": value,
        "chainId": chain_id,
        "key": idempotency_key,
    }
    RECORDED_ACTIONS.append(action)
    return {"status": "accepted", "idempotencyKey": idempotency_key, "approvalId": len(RECORDED_ACTIONS)}


def buy(token, usd=None, idempotency_key=None, chain=None) -> ActionResult:
    ref, asset_chain = _ref_and_chain(token)
    chain_id = chain if chain is not None else asset_chain
    command = f"acp trade --token-in usdc --amount-in {usd} --token-out {ref}"
    if chain_id is not None:
        command += f" --chain-out {chain_id}"
    summary = f"bought {usd} USD of {ref}" if usd is not None else f"bought {ref}"
    return _record_money_action("buy", command, idempotency_key, summary, asked=usd, token=ref, chainId=chain_id)


def sell(token, usd=None, pct=None, all=False, idempotency_key=None, chain=None) -> ActionResult:
    ref, asset_chain = _ref_and_chain(token)
    chain_id = chain if chain is not None else asset_chain
    if all:
        amount = "--pct 100"
        asked = "all"
    elif pct is not None:
        amount = f"--pct {pct}"
        asked = pct
    else:
        amount = f"--amount-in {usd}"
        asked = usd
    command = f"acp trade --token-in {ref} {amount} --token-out usdc"
    if chain_id is not None:
        command += f" --chain-in {chain_id}"
    summary = f"sold {'all of ' if all else ''}{ref}"
    return _record_money_action("sell", command, idempotency_key, summary, asked=asked, token=ref, chainId=chain_id)


def long(token, usd=None, leverage=1, idempotency_key=None) -> ActionResult:
    ref, asset_chain = _ref_and_chain(token)
    command = f"acp perptrade --side long --token {ref} --amount-usdc {usd} --leverage {leverage}"
    summary = f"opened {leverage}x long {ref} with {usd} USD margin"
    return _record_money_action("long", command, idempotency_key, summary, asked=usd, token=ref, leverage=leverage)


def short(token, usd=None, leverage=1, idempotency_key=None) -> ActionResult:
    ref, asset_chain = _ref_and_chain(token)
    command = f"acp perptrade --side short --token {ref} --amount-usdc {usd} --leverage {leverage}"
    summary = f"opened {leverage}x short {ref} with {usd} USD margin"
    return _record_money_action("short", command, idempotency_key, summary, asked=usd, token=ref, leverage=leverage)


def close(token, idempotency_key=None) -> ActionResult:
    ref, asset_chain = _ref_and_chain(token)
    command = f"acp perptrade --close --token {ref}"
    summary = f"closed {ref}"
    return _record_money_action("close", command, idempotency_key, summary, asked=None, token=ref)


def stock_buy(ticker, usd=None, idempotency_key=None) -> ActionResult:
    command = f"acp stocktrade --side buy --ticker {ticker} --amount-usdc {usd}"
    summary = f"bought {usd} USD of {ticker}"
    return _record_money_action("stock_buy", command, idempotency_key, summary, asked=usd, token=ticker)


def stock_sell(ticker, usd=None, pct=None, all=False, idempotency_key=None) -> ActionResult:
    if all:
        amount = "--pct 100"
        asked = "all"
    elif pct is not None:
        amount = f"--pct {pct}"
        asked = pct
    else:
        amount = f"--amount-usdc {usd}"
        asked = usd
    command = f"acp stocktrade --side sell --ticker {ticker} {amount}"
    summary = f"sold {'all of ' if all else ''}{ticker}"
    return _record_money_action("stock_sell", command, idempotency_key, summary, asked=asked, token=ticker)


def read(path: str, params: dict | None = None):
    """Answer a bevo.read(...) call from fixtures/<slug>.json.

    The fixture file name is derived from the last non-empty path segment,
    e.g. "/me" -> me.json, "/user-assets" -> user-assets.json.
    """
    slug = path.strip("/").split("/")[-1] or "index"
    fp = _fixture_path(f"{slug}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for read({path!r}) — expected {fp}")
    return json.loads(fp.read_text())


def rpc(chain_id, method, params=None):
    """Answer a bevo.rpc(...) call from fixtures/rpc-<method>.json."""
    fp = _fixture_path(f"rpc-{method}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for rpc(chain_id={chain_id}, method={method!r}) — expected {fp}")
    return json.loads(fp.read_text())


def exec_status(key: str, route: str = "trade"):
    for action in RECORDED_ACTIONS:
        if action.get("key") == key:
            return {"state": "executed", "route": route, "idempotencyKey": key}
    return {"state": "unknown", "route": route, "idempotencyKey": key}


def escalate(reason: str, events_list) -> dict:
    action = {"call": "escalate", "reason": reason, "events": events_list, "key": None}
    RECORDED_ACTIONS.append(action)
    return {"status": "escalated"}
