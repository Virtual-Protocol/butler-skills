"""stub_bevo.py — the offline `bevo` module.

A port of bevo-docker's `api/bevo_services/sdk_rehearsal.py` recording
semantics: `events()`/`trades()` replay a fixture JSONL file, the money rails
(`trade`/`execute`) record their call (including the idempotency key) into a
list instead of acting, `read()`/`rpc()`/`holdings()` answer from fixture JSON
files, `balance()` and `is_stock()` answer from fixture/env-driven data,
`state` is a dict on disk in the replay's state directory, and `log()` just
prints. `replay.py` puts this module on `sys.path` as `bevo` so a skill's
real, unmodified `duty.py` can `import bevo` and run against captured data
with no network and no container.

`bevo.trade(command="acp trade …", idempotency_key=…)` is the ONE money rail;
the old per-verb shortcuts (`buy`/`sell`/`long`/`short`/`close`/`stock_buy`/
`stock_sell`) were each a one-line rewrite of an `acp trade` CLI string that
hid the grammar and drifted from it, and were deleted from the real SDK — this
stub no longer offers them either, so a skill that still calls one fails the
same way here as it would in a real container.

Fixtures live in `fixtures/` next to this file (BEVO_STUB_FIXTURES_DIR
overrides). When BEVO_STUB_FIXTURES_URL is set (replay.py sets it to the hub's
published fixtures directory) a fixture that is missing locally is downloaded
from `<url>/<name>` on first use — the only network this stub ever touches,
and only for files that are not already on disk.

`trade()`/`execute()` record every call unconditionally — this stub does no
idempotency-key dedup of its own. `replay.py` checks the recorded list
afterwards and fails the replay if a key is missing or reused, but it builds
its `seen_keys` map fresh in every process (`replay.py`'s `main()`), so that
check catches key reuse WITHIN ONE RUN only. Re-running the same fixture
re-emits the same keys and records the same actions again, and that is
correct: cross-run safety is the real container's SERVER-SIDE per-key dedup at
`POST /butler-exec/trade`, which answers "already filed" instead of trading
twice. This stub deliberately does not model that — a duty must be safe
because its keys are derived from the event, not because something remembered
them.

`trade(command=…)` is grammar-checked here (`_check_trade_command`) the way
bevo-docker's duty shim refuses a malformed command, so a shape the real rails
would reject fails in the replay instead of at the venue.

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
    """A token on a chain, the way the trade feed names it and the way a
    `bevo.trade(command=…)` string has to spell it: `.ref` goes after
    `--token-in` / `--token-out`, `.chain_id` after `--chain-in` /
    `--chain-out`."""

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
        """What the command's token flag carries: the address when there is
        one, else the symbol. A leg with no address is a bare ticker — a
        tokenized stock (its own grammar) or an unresolved symbol, never a
        `--token-out` on the swap rail."""
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


# --- money rail: trade() / execute() -------------------------------------------------------

# Amount flags whose value must be a positive number. A quantity of 0 (or a
# negative one, or one that formatted to garbage) is the failure mode the
# removed money verbs used to absorb: the server answers a parse error rather
# than a reason, so refuse it here where the message can say which flag.
_AMOUNT_FLAGS = ("--amount-in", "--amount-usdc", "--amount-shares", "--size")


def _flag_value(tokens, flag):
    """The token after `flag`, or None. Exact-token match: `--chain` must never
    be satisfied by `--chain-in` / `--chain-out`, which are a different rail."""
    for i, token in enumerate(tokens):
        if token == flag:
            return tokens[i + 1] if i + 1 < len(tokens) else None
    return None


def _check_trade_command(command, message):
    """Refuse a `bevo.trade()` call the real rails would refuse.

    A minimal mirror of bevo-docker's duty shim (`api/scripts/bevo-duty-shim.py`,
    the retired-rail refusals): the command string is now the only place the
    grammar lives, so a stub that accepted any string would let a duty pass its
    replay and fail at the venue. Raises BevoError naming the flag at fault.
    """
    if message is not None:
        raise BevoError(
            "bevo.trade(message=…) is free text, not a trade — the money rail takes a "
            'command: bevo.trade(command="acp trade …", idempotency_key=…)'
        )
    if command is None:
        return
    if not isinstance(command, str) or not command.strip():
        raise BevoError(f"bevo.trade(command=…) must be a non-empty `acp trade` string, got {command!r}")

    text = command.strip()
    if not text.startswith("acp trade"):
        raise BevoError(
            f"bevo.trade(command={text!r}) must start with `acp trade` — "
            "`acp wallet send-transaction` calldata goes through bevo.execute(to, data, …)"
        )

    tokens = text.split()
    flags = set(tokens)

    if "--amount-in" in flags and "--amount-usdc" in flags:
        raise BevoError(
            f"bevo.trade(command={text!r}): --amount-in (a swap's input QUANTITY) and "
            "--amount-usdc (a perp/stock's USD size) are different grammars — send one"
        )
    if "--side" in flags and "--token-in" in flags:
        raise BevoError(
            f"bevo.trade(command={text!r}): a --side order is a perp — it takes --token <SYM>, "
            "never the swap grammar's --token-in/--token-out"
        )
    if "--token" in flags and "--amount-shares" in flags:
        venue = _flag_value(tokens, "--chain")
        if venue is None:
            raise BevoError(
                f"bevo.trade(command={text!r}): a stock sell must name its venue with "
                "--chain <eth|sol>, from the holding's own spot.stocks[] row"
            )
        if venue.isdigit():
            raise BevoError(
                f"bevo.trade(command={text!r}): --chain {venue} is a chain id — a stock sell takes "
                "the VENUE NAME (eth|sol); a numeric one is rerouted onto a bare-symbol spot swap, "
                "which is a different asset"
            )

    for flag in _AMOUNT_FLAGS:
        if flag not in flags:
            continue
        raw = _flag_value(tokens, flag)
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            raise BevoError(f"bevo.trade(command={text!r}): {flag} needs a number, got {raw!r}") from None
        if amount <= 0:
            raise BevoError(
                f"bevo.trade(command={text!r}): {flag} is {raw} — refuse a quantity of 0 or less "
                "rather than sending it"
            )


def trade(command=None, params=None, message=None, idempotency_key=None, max_attempts=3) -> dict:
    _check_trade_command(command, message)
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


def read(path: str, params: dict | None = None):
    """Answer a bevo.read(...) call from fixtures/<slug>.json.

    The fixture file name is derived from the last non-empty path segment with
    any query string dropped, e.g. "/me" -> me.json, "/user-assets" and
    "/user-assets?fresh=1" both -> user-assets.json. `fresh=1` is load-bearing
    on the live rails — without it bevo-server's stale-while-revalidate cache
    can serve a PRE-trade balance for up to ten minutes — but it selects no
    different fixture here, so a duty that reads it either way gets an answer.

    `params` narrows nothing: fixtures are per-path, not per-parameter, so a
    duty must match the row it wants inside the answer (on address, ticker or
    coin) exactly as it would against the live endpoint.
    """
    slug = path.strip("/").split("/")[-1].split("?")[0] or "index"
    fp = _fixture_path(f"{slug}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for read({path!r}) — expected {fp}")
    return json.loads(fp.read_text())


# The wallet read the live SDK sizes money off (bevo-docker sdk.py `_ASSETS_PATH`).
_ASSETS_PATH = "/user-assets?fresh=1"


class Holding:
    """One spot holding, the way bevo.holdings() hands it over — the live SDK's
    Holding (bevo-docker `sdk_types.py`) over a `spot.tokens[]` row.

    A tokenized stock is NOT here: it has its own `spot.stocks[]` array, whose
    `shares` disagrees with this raw on-chain `amount` on a share-multiplier
    venue. Size a stock off that array, through bevo.read("/user-assets")."""

    def __init__(self, row: dict):
        symbol = row.get("symbol")
        self.symbol = str(symbol).upper() if symbol else None
        self.amount = _to_float(row.get("balance"))
        self.usd = _to_float(row.get("usdValueUsd"))
        self.price_usd = _to_float(row.get("usdPrice"))
        address = row.get("tokenAddress")
        address = str(address) if address else None
        self.address = address.lower() if address and address.startswith("0x") else address
        chain_id = row.get("chainId")
        self.chain_id = int(chain_id) if chain_id not in (None, "") else None
        network = row.get("network")
        self.chain = str(network).lower() if network else None

    def __repr__(self):
        return f"Holding(symbol={self.symbol}, amount={self.amount}, chain_id={self.chain_id})"


def _to_float(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def holdings() -> list[Holding]:
    """The owner's spot holdings, from the same fixtures/user-assets.json that
    answers bevo.read("/user-assets"). Empty when there is no fixture — the
    live SDK returns [] when the wallet could not be read, and a duty must tell
    that from holding nothing with bevo.balance().available, never by assuming
    a zero."""
    try:
        body = read(_ASSETS_PATH)
    except BevoError:
        return []
    spot = body.get("spot") if isinstance(body, dict) else None
    rows = spot.get("tokens") if isinstance(spot, dict) else None
    return [Holding(r) for r in rows or [] if isinstance(r, dict)]


class _State(dict):
    """`bevo.state` — the live SDK's dict-on-disk, saved on every write.

    The file is `state.json` in the process's working directory (BEVO_STATE_PATH
    overrides), which is the replay's `--state-dir`: replay.py chdir's there
    before running duty.py, so the path is resolved LAZILY on first touch, not
    at import time when the cwd is still the caller's."""

    def __init__(self):
        super().__init__()
        self._loaded = False

    def _path(self) -> str:
        return os.environ.get("BEVO_STATE_PATH") or os.path.join(os.getcwd(), "state.json")

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            dict.update(self, data)

    def _save(self):
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(dict(self), f)
        except OSError as exc:
            log(f"[stub_bevo] state not saved: {exc}")

    def __getitem__(self, key):
        self._load()
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._load()
        return dict.__contains__(self, key)

    def __iter__(self):
        self._load()
        return dict.__iter__(self)

    def __len__(self):
        self._load()
        return dict.__len__(self)

    def get(self, key, default=None):
        self._load()
        return dict.get(self, key, default)

    def keys(self):
        self._load()
        return dict.keys(self)

    def values(self):
        self._load()
        return dict.values(self)

    def items(self):
        self._load()
        return dict.items(self)

    def __setitem__(self, key, value):
        self._load()
        dict.__setitem__(self, key, value)
        self._save()

    def __delitem__(self, key):
        self._load()
        dict.__delitem__(self, key)
        self._save()

    def setdefault(self, key, default=None):
        self._load()
        if key not in self:
            self[key] = default
            return default
        return dict.__getitem__(self, key)

    def pop(self, key, *args):
        self._load()
        value = dict.pop(self, key, *args)
        self._save()
        return value

    def update(self, *args, **kwargs):
        self._load()
        dict.update(self, *args, **kwargs)
        self._save()


state = _State()


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
