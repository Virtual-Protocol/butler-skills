"""stub_bevo.py — the offline `bevo` module.

A replay stand-in for `virtuals-agent`'s `src/integrations/butler/bin/py/bevo.py`:
the typed waiters (`events`/`trades`/`messages`/`transfers`/`ticks`/`polls`/
`webhooks`/`frames`/`batches`/`typed`) replay a fixture JSONL file, `read()`/
`rpc()` answer from fixture JSON files, `balance()`/`holdings()`/`stocks()`/
`positions()` are read()'s own answer reshaped, `state` is a dict on disk in
the replay's state directory, `allow()` is the same UTC-bucket rate limiter,
`log()`/`notify()` record instead of reaching anyone, and `prompt()`/
`decide()` always raise `BevoError(code="rehearsal")` — exactly what the real
SDK does in `BEVO_MODE=rehearsal`, because a replay must never call a real
model. `replay.py` puts this module on `sys.path` as `bevo` so a template's
real, unmodified `duty.py` can `import bevo` and run against captured data
with no network and no container.

**There is no money verb.** Since 2026-09-21 a duty spends by shelling
`acp trade`/`acp wallet send-transaction`/`acp card issue` directly — see
`bevo_ast.py`'s `RETIRED` set in `virtuals-agent`. This stub therefore does
not offer `trade()`/`execute()`/`buy()`/... at all; a duty.py that still
calls one fails here with an `AttributeError`, the same way it would in a
real container. What this stub DOES do is monkeypatch `subprocess.run` (and
`.check_output`/`.Popen`/`.call`/`.check_call`) at import time: an argv whose
first two elements are literally `"acp"` and a money subcommand
(`trade`/`wallet`/`card`) is intercepted and RECORDED instead of actually
spawning the real CLI, and a synthetic `{"status": "accepted", ...}` JSON
reply is returned on stdout — everything else passes through to the real
`subprocess` so a duty's other shell-outs (there should not be any; the
validator refuses most of them) behave normally.

Fixtures live in `fixtures/` next to this file (BEVO_STUB_FIXTURES_DIR
overrides). When BEVO_STUB_FIXTURES_URL is set (replay.py sets it to the hub's
published fixtures directory) a fixture that is missing locally is downloaded
from `<url>/<name>` on first use — the only network this stub ever touches,
and only for files that are not already on disk.

Python 3.11 stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess as _real_subprocess
import time
from pathlib import Path

FIXTURES_DIR = Path(os.environ.get("BEVO_STUB_FIXTURES_DIR", str(Path(__file__).parent / "fixtures")))
FIXTURE_NAME = os.environ.get("BEVO_STUB_FIXTURE", "trade-activity-page")
FIXTURES_URL = os.environ.get("BEVO_STUB_FIXTURES_URL", "").rstrip("/")

SERVICE_ID = os.environ.get("BEVO_SERVICE_ID", "stub-service-id")
SESSION_ID = os.environ.get("BEVO_SESSION_ID", "stub-session-id")

RECORDED_ACTIONS: list[dict] = []

MONEY_BIN = "acp"
MONEY_SUBCOMMANDS = ("trade", "wallet", "card")
IDEMPOTENCY_FLAG = "--idempotency-key"


class BevoError(Exception):
    def __init__(self, message, code=None, retry_after_s=None):
        super().__init__(message)
        self.code = code
        self.retry_after_s = retry_after_s


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


# --- the event stream ----------------------------------------------------------------------


def _events_raw():
    path = _fixture_path(f"{FIXTURE_NAME}.jsonl")
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)



# ── vendored types ───────────────────────────────────────────────────────────
#
# Verbatim from `src/integrations/butler/bin/py/bevopy` in
# Virtual-Protocol/virtuals-agent — the container that actually runs a duty.
#
# INLINED rather than imported because every published tool is a single
# stdlib-only file an author can curl and run (test_published_tools_are_
# single_files_using_only_the_stdlib pins that). Vendored rather than
# re-implemented because the waiters must yield what production yields: these
# classes carry DERIVED properties (`is_stock`, `coin`, `is_short`) that an
# attribute-view over the raw dict would answer None for — silently, which is
# how a template skips every event while the replay reports a pass.
#
# Re-copy when the container's copy changes. The two drifting is the failure
# this block exists to prevent.
# ─────────────────────────────────────────────────────────────────────────────

"""Typed views over the events a duty receives.

Read-only objects with named attributes, rather than the raw dicts. The
reason is one specific failure: a duty that reads `trade.tokenOut` — a name
the feed has never used — gets `None` in a dict world, silently sizes a trade
off it, and files something nobody asked for. Here it raises, and the message
names the attributes that do exist.

    >>> trade.tokenOut
    AttributeError: TradeEvent has no attribute 'tokenOut' — a trade event
    has: id, owner, wallet, direction, token_in, token_out, ...

Materialised beside `bevo.py` in every duty's run directory, so it stays
stdlib-only and imports nothing from the server.
"""

import math
import re

from datetime import datetime, timedelta, timezone

#: Solana's chain id in Bevo's numbering. Not an EVM id; kept because a
#: copy-trade duty comparing `chain_id` will meet it.
SOLANA_CHAIN_ID = 1151111081099710

#: The tokenized-stock command shape: `--token SYM`, never `--token-in/-out`.
_STOCK_COMMAND = re.compile(r"(^|\s)--token\s")


def _lower(value):
    return str(value).lower() if isinstance(value, str) else value


def _upper(value):
    return str(value).upper() if isinstance(value, str) else value


def _address(value):
    """EVM addresses lowercase; Solana mints are case-sensitive base58."""
    if not isinstance(value, str):
        return value
    return value.lower() if value.startswith("0x") else value


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chain_id(value):
    """The feed sends chain ids as strings, because JS cannot hold Solana's."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _ts(value):
    if not isinstance(value, str) or value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class _Typed:
    """A read-only view whose unknown attributes raise informatively."""

    __slots__ = ("raw",)
    _label = "event"
    _fields = ()

    def __init__(self, raw):
        object.__setattr__(self, "raw", raw if isinstance(raw, dict) else {})

    def __setattr__(self, name, value):
        raise AttributeError(f"{type(self).__name__} is read-only")

    def __getattr__(self, name):
        for attr, source in type(self)._fields:
            if attr != name:
                continue
            return source(self.raw) if callable(source) else self.raw.get(source)
        valid = ", ".join(attr for attr, _ in type(self)._fields)
        raise AttributeError(
            f"{type(self).__name__} has no attribute {name!r} — "
            f"a {type(self)._label} has: {valid}"
        )

    def __dir__(self):
        return sorted([attr for attr, _ in type(self)._fields] + ["raw", "as_dict"])

    def as_dict(self):
        return dict(self.raw)

    def __repr__(self):
        pairs = ", ".join(f"{a}={getattr(self, a)!r}" for a, _ in type(self)._fields[:6])
        return f"{type(self).__name__}({pairs}…)"


class Asset:
    """A token *and* the chain it lives on.

    The pair matters: the same symbol exists on several chains, and a copy
    that resolves the symbol on the wrong one buys a different thing. Pass
    Put this on the wire rather than a bare symbol whenever you have it.
    """

    __slots__ = ("symbol", "address", "chain_id")

    def __init__(self, symbol, address, chain_id):
        object.__setattr__(self, "symbol", _upper(symbol))
        object.__setattr__(self, "address", _address(address))
        object.__setattr__(self, "chain_id", chain_id)

    def __setattr__(self, name, value):
        raise AttributeError("Asset is read-only")

    @property
    def ref(self):
        """What identifies it on the wire: the address if known, else the symbol."""
        return self.address or self.symbol

    def __eq__(self, other):
        return (
            isinstance(other, Asset)
            and self.ref == other.ref
            and self.chain_id == other.chain_id
        )

    def __hash__(self):
        return hash((self.ref, self.chain_id))

    def __str__(self):
        head = self.symbol or self.address or "?"
        return f"{head}@{self.chain_id}" if self.chain_id else str(head)

    def __repr__(self):
        return f"Asset({self})"


class TradeEvent(_Typed):
    """One row of the public trade feed."""

    __slots__ = ()
    _label = "trade event"
    _fields = (
        ("id", "id"),
        ("owner", "principalId"),
        ("wallet", lambda r: _lower(r.get("walletAddress"))),
        ("owner_wallet", lambda r: _lower(r.get("ownerWalletAddress"))),
        ("type", "type"),
        ("direction", lambda r: _lower(r.get("direction"))),
        ("token_in", lambda r: _upper(r.get("tokenInSymbol"))),
        ("token_out", lambda r: _upper(r.get("tokenOutSymbol"))),
        ("token_in_address", lambda r: _address(r.get("tokenInAddress"))),
        ("token_out_address", lambda r: _address(r.get("tokenOutAddress"))),
        ("amount_in", lambda r: _num(r.get("amountIn"))),
        ("usd_value", lambda r: _num(r.get("usdValue"))),
        ("leverage", lambda r: _num(r.get("leverage"))),
        ("chain_id", lambda r: _chain_id(r.get("chainId"))),
        ("tx_hash", "txHash"),
        ("command", "command"),
        ("reduce_only", "reduceOnly"),
        ("hl_event", "hlEvent"),
        ("at", lambda r: _ts(r.get("createdAt"))),
    )

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
    def token(self):
        """The interesting side, for display. Not for trading — use `asset`."""
        return self.token_out if self.is_buy else self.token_in

    @property
    def asset(self):
        """What this trade was *about*, ready to pass to a money verb.

        A buy is identified by what came out, a sell by what went in. Getting
        this backwards makes every copy trade buy USDC.
        """
        if self.is_buy:
            return Asset(self.token_out, self.token_out_address, self.chain_id)
        return Asset(self.token_in, self.token_in_address, self.chain_id)

    @property
    def is_stock(self):
        """A tokenized stock, recognised from the COMMAND's shape.

        Not from the token catalog: `--token SYM` with no `--side` is the
        stock grammar and `--token-in/--token-out` is the spot one, so the
        command the leader actually sent is the only place the distinction
        survives the feed. The catalog test this replaced called a
        newly-listed stock a spot token and mirrored it onto the wrong rail.
        """
        return not self.is_perp and bool(_STOCK_COMMAND.search(str(self.command or "")))

    @property
    def side(self):
        """'long' | 'short' | None — the perp side the direction names.

        Hyperliquid rows spell direction `long`/`short`; the spot feed spells
        it `buy`/`sell`. Both map here so a mirrored open reads the same way
        whichever the leader's venue used. `None` when the row named neither,
        which a perp open must refuse rather than default.
        """
        if self.direction in ("short", "sell"):
            return "short"
        if self.direction in ("long", "buy"):
            return "long"
        return None

    @property
    def is_short(self):
        """`False` for spot, a bool for a perp, **`None` for a perp with no side**.

        The three-valued answer is load-bearing: a perp open must refuse a row
        whose side it could not read rather than guess `long`, so the caller
        tests `isinstance(ev.is_short, bool)` and not the truthiness.
        """
        if not self.is_perp:
            return False
        side = self.side
        return None if side is None else side == "short"

    @property
    def is_close(self):
        """A perp row that REDUCES a position — including one the venue closed.

        A liquidation and an exchange close are closes nobody asked for, and a
        mirror that treats them as opens doubles down on the position that was
        just taken away.
        """
        return bool(
            self.is_perp
            and (self.reduce_only is True or self.hl_event in ("liquidation", "exchange_close"))
        )

    @property
    def coin(self):
        """What the trade was about, as a bare ticker.

        A perp names its coin on whichever leg carries it; a spot buy names it
        on the way out and a sell on the way in. The leading `$` some feeds
        prefix is stripped — `$BTC` and `BTC` are one coin, and two spellings
        are two idempotency keys.
        """
        if self.is_perp:
            raw = self.token_out or self.token_in
        elif self.is_buy:
            raw = self.token_out
        else:
            raw = self.token_in
        return str(raw).lstrip("$") if isinstance(raw, str) else raw


class GroupMessage(_Typed):
    __slots__ = ()
    _label = "group message"
    _fields = (
        ("id", "id"),
        ("group_id", "groupId"),
        ("channel_id", "channelId"),
        ("sender", lambda r: r.get("senderId") or r.get("senderPrincipalId")),
        ("sender_name", "senderDisplayName"),
        ("text", lambda r: r.get("content") or ""),
        ("content_type", "contentType"),
        ("encrypted", lambda r: bool(r.get("encrypted"))),
        ("edited", lambda r: bool(r.get("edited"))),
        ("at", lambda r: _ts(r.get("createdAt"))),
    )


class WalletTransfer(_Typed):
    __slots__ = ()
    _label = "wallet transfer"
    _fields = (
        ("id", "id"),
        ("tx_hash", "txHash"),
        ("chain_id", lambda r: _chain_id(r.get("chainId"))),
        ("direction", "direction"),
        ("amount", lambda r: _num(r.get("amount"))),
        ("symbol", lambda r: _upper(r.get("symbol"))),
        ("token_address", lambda r: _address(r.get("tokenAddress"))),
        ("counterparty", lambda r: _lower(r.get("counterparty"))),
        ("counterparty_handle", "counterpartyHandle"),
        ("at", lambda r: _ts(r.get("timestamp") or r.get("at") or r.get("createdAt"))),
    )

    @property
    def is_incoming(self):
        return self.direction == "in"


def _iso_ms(when):
    """A JS `Date.toISOString()`, byte for byte.

    The slot below is an idempotency key that bevo-server's ledger already
    holds strings from — minted by the retired graph stage, which wrote
    whatever `toISOString()` produced. `2026-01-02T03:00:00.000Z`: always
    three fractional digits, always `Z`. Python's own `isoformat()` drops the
    fraction when it is zero and writes `+00:00`, and either difference mints
    a SECOND key for a slot already filed.
    """
    utc = when.astimezone(timezone.utc)
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{utc.microsecond // 1000:03d}Z"


class TimerTick(_Typed):
    __slots__ = ()
    _label = "timer tick"
    _fields = (
        ("at", lambda r: _ts(r.get("at"))),
        ("daily_at", "dailyAt"),
        ("timezone", "timezone"),
        ("interval_seconds", "intervalSeconds"),
    )

    @property
    def slot(self):
        """The schedule SLOT this fire belongs to — not the instant it fired.

        The difference is the whole point. A duty scheduled daily at 14:00
        that the supervisor catches up on after a restart fires twice, at
        14:00:00 and at 14:07:31; keyed on the instant those are two buys,
        keyed on the slot they are one, and bevo-server's ledger answers the
        second one `replay`. Derived from the duty's OWN trigger row (which
        the supervisor puts on the tick) rather than from the caller's clock.

        `None` when the fire carries no time at all, which the caller must
        treat as "no key to derive" rather than as a slot named `None`.
        """
        when = self.at
        if when is None:
            return None
        daily = self.daily_at
        if isinstance(daily, str) and daily != "":
            zone = self.timezone if isinstance(self.timezone, str) and self.timezone else "UTC"
            day_key = when.astimezone(_zone(zone)).strftime("%Y-%m-%d")
            return f"{day_key}T{daily}" + ("Z" if zone == "UTC" else f"@{zone}")
        interval = _num(self.interval_seconds)
        if interval is not None and interval > 0:
            epoch = when.timestamp()
            floored = math.floor(epoch / interval) * interval
            return _iso_ms(datetime.fromtimestamp(floored, tz=timezone.utc))
        return _iso_ms(when)


def _zone(name):
    """A named zone, falling back to UTC.

    `zoneinfo` is stdlib but its DATABASE is not always present (a slim image
    with no `tzdata`), and a duty must not die on a timezone it cannot look
    up — it files under the UTC day instead, which is wrong by hours at worst
    and still stable across restarts.
    """
    if name == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _watched_json(text, what):
    """Parse, or return None. A duty must not die on one malformed body."""
    import json

    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


class HttpPollEvent(_Typed):
    """One fetch of an `http_poll` trigger. Delivered only when it changed."""

    __slots__ = ()
    _label = "http_poll fetch"
    _fields = (
        ("url", "url"),
        ("status", "status"),
        ("body", lambda r: r.get("body") or ""),
        ("at", lambda r: _ts(r.get("at"))),
    )

    def json(self):
        return _watched_json(self.body, f"the response from {self.url}")


class WebhookEvent(_Typed):
    __slots__ = ()
    _label = "webhook delivery"
    _fields = (
        ("id", "id"),
        ("content_type", "contentType"),
        ("body", lambda r: r.get("body") or ""),
        ("at", lambda r: _ts(r.get("receivedAt") or r.get("at"))),
    )

    def json(self):
        return _watched_json(self.body, "the webhook body")


class WebsocketFrame(_Typed):
    """One text frame.

    `text` reads `data` as a fallback because bevo's supervisor emitted the
    payload under `message` while its own type read `text` — so every live
    frame arrived empty. Ours emits `text`; the fallback is for a duty
    written against the old shape.
    """

    __slots__ = ()
    _label = "websocket frame"
    _fields = (
        ("url", "url"),
        ("text", lambda r: r.get("text") or r.get("data") or r.get("message") or ""),
        ("at", lambda r: _ts(r.get("at"))),
    )

    def json(self):
        return _watched_json(self.text, f"the frame from {self.url}")


class Balance(_Typed):
    """The owner's cash and totals.

    `available` is the field to check first. A failed read is **not** a zero
    balance, and sizing a trade off one is how a duty spends nothing — or
    everything.

    `cash_usd` is read under two names. The deployed server answers `cashUsd`;
    `spotUsdcUsd` is the spelling in the bevo-server checkout and in no
    response this container has received, and reading only that one made
    `cash_usd` `None` for every code duty from 2026-09-10. Neither is trusted
    over the other, so sizing survives whichever way that settles.

    Note `available` is `or`-based and `totalUsd` arrives even when the cash
    figure does not — so it reads `True` while `cash_usd` is `None`, and
    `b.cash_usd * 0.2` then raises. Check the figure you are about to use, not
    just `available`.
    """

    __slots__ = ()
    _label = "balance"
    _fields = (
        ("total_usd", lambda r: _num(r.get("totalUsd"))),
        ("cash_usd", lambda r: _num(r.get("cashUsd") if r.get("cashUsd") is not None else r.get("spotUsdcUsd"))),
        ("perps_usd", lambda r: _num(r.get("hlAccountUsd"))),
        ("spot", "spot"),
        ("perps", "perps"),
    )

    @property
    def available(self):
        return self.total_usd is not None or self.cash_usd is not None

    def __bool__(self):
        """`if balance:` asks whether the read WORKED, not whether it is > 0.

        `bevo.balance()` answers `Balance({})` on a failed read, and an object
        is truthy — so the guard a duty author actually writes let an
        unreadable balance straight through to `f"${b.total_usd:,.2f}"`, which
        raises `TypeError` on `None`. A duty that crashes on the line after the
        check is worse than one that never checked. A real zero balance still
        reads `True`: `available` turns on the figure being *present*, not on
        it being non-zero.
        """
        return self.available


class Holding(_Typed):
    __slots__ = ()
    _label = "holding"
    _fields = (
        ("symbol", lambda r: _upper(r.get("symbol"))),
        ("amount", lambda r: _num(r.get("balance"))),
        ("usd", lambda r: _num(r.get("usdValueUsd"))),
        ("price_usd", lambda r: _num(r.get("usdPrice"))),
        ("address", lambda r: _address(r.get("tokenAddress"))),
        ("chain_id", lambda r: _chain_id(r.get("chainId"))),
    )

    @property
    def asset(self):
        return Asset(self.symbol, self.address, self.chain_id)


def _venue(raw):
    """A stock's venue is a name (`eth`, `sol`), never a chain id.

    `None` for an empty or all-numeric value rather than passing it through:
    bevo-server reroutes a numeric `--chain` onto a bare-symbol spot swap,
    which is a different asset entirely. The portfolio row spells it `chain`
    and the port spells it `network`, so take both.
    """
    text = str((raw.get("network") or raw.get("chain") or "")).strip().lower()
    return text if text and not text.isdigit() else None


class StockHolding(_Typed):
    """A tokenized-stock position, which is NOT a `Holding`.

    A stock lives in its own `spot.stocks` array and the two disagree **on
    purpose**: `spot.tokens` carries the raw on-chain balance, `shares` is what
    the venue sells, and on a share-multiplier venue like xStocks they differ.
    Sizing a sell off the look-alike token row sells the wrong quantity, which
    is why this is a separate type rather than a flag on `Holding`.
    """

    __slots__ = ()
    _label = "stock holding"
    _fields = (
        ("ticker", lambda r: _upper(r.get("ticker"))),
        ("shares", lambda r: _num(r.get("shares"))),
        ("usd_per_share", lambda r: _num(r.get("usdPerShare"))),
        ("usd", lambda r: _num(r.get("usdValueUsd"))),
        ("venue", _venue),
    )


class PerpPosition(_Typed):
    """One open Hyperliquid position.

    `coin` is the FULL id and is what a close has to send back: HIP-3
    namespaces a coin as `<dex>:<coin>`, so a position opened as `xyz:BTC`
    closes as `xyz:BTC` and never as `BTC`.
    """

    __slots__ = ()
    _label = "perp position"
    _fields = (
        ("coin", lambda r: str(r.get("coin") or "") or None),
        ("side", lambda r: str(r.get("side") or "").lower() or None),
        ("size", lambda r: _num(r.get("size"))),
        ("usd", lambda r: _num(r.get("positionValueUsd"))),
        ("mark_usd", lambda r: _num(r.get("markPriceUsd"))),
    )

    @property
    def is_long(self):
        return self.side == "long"


#: Which payload key holds the event, per trigger kind.
KIND_TO_TYPE = {
    "trade": ("event", TradeEvent),
    "group": ("message", GroupMessage),
    "wallet": ("transfer", WalletTransfer),
    "timer": (None, TimerTick),
    "http_poll": (None, HttpPollEvent),
    "webhook": (None, WebhookEvent),
    "websocket": (None, WebsocketFrame),
}

# ── end vendored types ───────────────────────────────────────────────────────

def events():
    yield from _events_raw()


# The typed objects a waiter yields, vendored verbatim from the container's own
# `bevopy` (see tests/bevopy). Re-exported under the same names
# the real `bevo` module re-exports them under, because a duty.py may name one
# directly — `isinstance(ev, bevo.TradeEvent)` is how butler-skill-copytrade
# separates a trade row from anything else, and against a stub that yielded the
# raw dict it raised AttributeError while working in production.
Asset = Asset
Balance = Balance
GroupMessage = GroupMessage
Holding = Holding
HttpPollEvent = HttpPollEvent
PerpPosition = PerpPosition
StockHolding = StockHolding
TimerTick = TimerTick
TradeEvent = TradeEvent
WalletTransfer = WalletTransfer
WebhookEvent = WebhookEvent
WebsocketFrame = WebsocketFrame


def _typed_kind(kind: str):
    """Yield the same typed object the container's SDK would yield.

    `KIND_TO_TYPE` names both the envelope key the row sits under and the class
    that wraps it, so this stays right when the container adds a kind — where a
    hand-rolled `event or message or transfer` chain silently yielded the whole
    envelope for any kind it did not know.
    """
    key, cls = KIND_TO_TYPE[kind]
    for event in _events_raw():
        if event.get("kind") != kind:
            continue
        row = event if key is None else (event.get(key) or {})
        yield cls(row)


def trades():
    return _typed_kind("trade")


def messages():
    return _typed_kind("group")


def transfers():
    return _typed_kind("wallet")


def ticks():
    return _typed_kind("timer")


def polls():
    return _typed_kind("http_poll")


def webhooks():
    return _typed_kind("webhook")


def frames():
    return _typed_kind("websocket")


def typed(event):
    """The typed object for one raw event dict, or the dict when unknown.

    Mirrors the container's own `bevo.typed()`. It used to return the event
    untouched, which made `isinstance(ev, bevo.TradeEvent)` false for EVERY
    row — so butler-skill-copytrade, whose whole loop is
    `for raw in batch: ev = bevo.typed(raw)`, skipped all six fixture rows with
    "a row this duty copies nothing from" and recorded zero actions. The replay
    exited 0 and looked like a pass while exercising none of the money path.
    """
    if not isinstance(event, dict):
        return event
    entry = KIND_TO_TYPE.get(event.get("kind"))
    if entry is None:
        return event
    payload_key, cls = entry
    return cls(event if payload_key is None else event.get(payload_key) or {})


def batches(seconds=3.0, max_events=100):
    """No timing to observe when replaying a file all at once: each event is
    its own batch, exactly like the real SDK's rehearsal mode."""
    for event in _events_raw():
        yield [event]


# --- logging and state -----------------------------------------------------------------------


def log(message) -> None:
    print(f"[stub_bevo.log] {message}")


class _State(dict):
    """`bevo.state` — the live SDK's dict-on-disk, saved on every write.

    The file is `state.json` in the process's working directory
    (BEVO_STATE_PATH overrides), which is the replay's `--state-dir`:
    replay.py chdir's there before running duty.py, so the path is resolved
    LAZILY on first touch, not at import time when the cwd is still the
    caller's."""

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


def allow(key, per_day=None, per_hour=None, max_usd=None, usd=0):
    """A port of the real UTC-bucket rate limiter — see bevo.py. Consumes on
    True, before the spend is attempted."""
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    hour = time.strftime("%Y-%m-%dT%H", time.gmtime(now))
    slot = state.get(f"_allow:{key}") or {}

    if slot.get("day") != day:
        slot = {"day": day, "day_n": 0, "day_usd": 0.0, "hour": hour, "hour_n": 0}
    if slot.get("hour") != hour:
        slot["hour"] = hour
        slot["hour_n"] = 0

    spend = float(usd or 0)
    if per_day is not None and slot.get("day_n", 0) >= int(per_day):
        return False
    if per_hour is not None and slot.get("hour_n", 0) >= int(per_hour):
        return False
    if max_usd is not None and float(slot.get("day_usd", 0.0)) + spend > float(max_usd):
        return False

    slot["day_n"] = slot.get("day_n", 0) + 1
    slot["hour_n"] = slot.get("hour_n", 0) + 1
    slot["day_usd"] = float(slot.get("day_usd", 0.0)) + spend
    state[f"_allow:{key}"] = slot
    return True


def sleep(seconds) -> None:
    """A replay never actually sleeps — there is no live clock to wait on."""
    return None


# --- talking to the outside --------------------------------------------------------------


def notify(text, quiet=False) -> dict:
    action = {"call": "notify", "text": str(text)[:500], "quiet": bool(quiet)}
    RECORDED_ACTIONS.append(action)
    return {"ok": True}


def escalate(reason, events=None) -> dict:
    """Retired shim — mirrors the real SDK's: logs and returns, never raises,
    so an old duty on a volume degrades instead of crashing. A NEW duty that
    calls it is refused at filing time (scripts/validate.py)."""
    log("[stub_bevo] escalate is retired — use bevo.prompt()")
    return {"accepted": False, "error": "escalate is retired"}


def prompt(text, *, system=None, schema=None, max_tokens=None):
    """A replay never calls a real model — exactly like BEVO_MODE=rehearsal,
    this always raises, so unguarded code fails here rather than in
    production."""
    raise BevoError(
        "replay never calls the model; live, this raises the same way when "
        "rate-limited or timed out — catch BevoError",
        code="rehearsal",
    )


def decide(question, options, *, context=None):
    options = [str(o) for o in (options or [])]
    if not 2 <= len(options) <= 12:
        raise ValueError("decide() needs between 2 and 12 options")
    return prompt(str(question))


# --- reads ---------------------------------------------------------------------------------


def read(path: str, params: dict | None = None):
    """Answer a bevo.read(...) call from fixtures/<slug>.json.

    The fixture file name is derived from the last non-empty path segment
    with any query string dropped, e.g. "/me" -> me.json,
    "/user-assets" and "/user-assets?fresh=1" both -> user-assets.json.
    `params` narrows nothing: fixtures are per-path, not per-parameter.
    """
    slug = str(path).strip("/").split("/")[-1].split("?")[0] or "index"
    fp = _fixture_path(f"{slug}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for read({path!r}) — expected {fp}")
    return json.loads(fp.read_text())


def rpc(chain_id, method, params=None):
    fp = _fixture_path(f"rpc-{method}.json")
    if not fp.exists():
        raise BevoError(f"no fixture for rpc(chain_id={chain_id}, method={method!r}) — expected {fp}")
    payload = json.loads(fp.read_text())
    return payload.get("result", payload)


# The four portfolio reads return the same TYPED objects the container returns.
# They used to return raw dicts (and `balance()` a `{"available", "raw"}` shape
# production has never had), so a template doing `row.size` or `bal.cash_usd`
# died with AttributeError under replay while working live —
# butler-skill-copytrade's perp-close leg is the one that caught it, on the
# trade-activity-mixed fixture. The `None`-vs-`[]` split is load-bearing and is
# copied too: an outage is not an empty portfolio, and a caller that cannot
# tell them apart sells the wrong quantity.


def balance():
    """Cash and totals as a `Balance`. An unavailable one on a failed read.

    Never raises, and never a zero: `Balance.__bool__` is `.available`, so the
    `if balance:` guard every template writes actually guards.
    """
    try:
        return Balance(read("/user-assets", {"fresh": 1}))
    except BevoError as exc:
        log(f"[bevo-sdk] balance unavailable: {exc}")
        return Balance({})


def holdings():
    """What the owner holds, as `Holding` objects. Empty on a failed read."""
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError as exc:
        log(f"[bevo-sdk] holdings unavailable: {exc}")
        return []
    tokens = ((body or {}).get("spot") or {}).get("tokens") or []
    return [Holding(t) for t in tokens]


def stocks():
    """Tokenized stocks as `StockHolding` objects; `None` when unreadable."""
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError as exc:
        log(f"[bevo-sdk] stocks unavailable: {exc}")
        return None
    spot = (body or {}).get("spot") or {}
    if spot.get("available") is not True:
        return None
    return [StockHolding(row) for row in (spot.get("stocks") or [])]


def positions():
    """Open perp positions as `PerpPosition` objects; `None` when unreadable."""
    try:
        body = read("/user-assets", {"fresh": 1})
    except BevoError as exc:
        log(f"[bevo-sdk] positions unavailable: {exc}")
        return None
    perps = (body or {}).get("perps") or {}
    if perps.get("available") is not True:
        return None
    return [PerpPosition(row) for row in (perps.get("positions") or [])]


def user(handle):
    try:
        return read("/user", {"username": str(handle).lstrip("@")})
    except BevoError:
        return None


def groups():
    try:
        return (read("/groups") or {}).get("groups") or []
    except BevoError:
        return []


def group_messages(group_id, since=None, limit=100):
    try:
        body = read(f"/groups/{int(group_id)}/messages", {"limit": limit})
    except BevoError:
        return []
    return list((body or {}).get("messages") or [])


def exec_status(key, route="trade"):
    for action in RECORDED_ACTIONS:
        if action.get("key") == key:
            return {"state": "executed", "route": route, "idempotencyKey": key}
    return {"state": "unknown", "route": route, "idempotencyKey": key}


# --- money: intercepting the shelled `acp` command ------------------------------------------


def _extract_key(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok == IDEMPOTENCY_FLAG and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _fake_completed_process(argv: list[str]):
    key = _extract_key(argv)
    action = {"call": "acp", "argv": list(argv), "key": key}
    RECORDED_ACTIONS.append(action)
    stdout = json.dumps({"status": "accepted", "idempotencyKey": key})
    return _real_subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")


def _is_acp_money_argv(argv) -> bool:
    return (
        isinstance(argv, (list, tuple))
        and len(argv) >= 2
        and argv[0] == MONEY_BIN
        and argv[1] in MONEY_SUBCOMMANDS
    )


# Captured BEFORE patching — `_real_subprocess` is the actual `subprocess`
# module object (the same one `import subprocess` gets elsewhere), so once
# its attributes are overwritten below, `_real_subprocess.run` would resolve
# to our own patch and recurse forever. These names are the only way back to
# the real implementations.
_ORIG_RUN = _real_subprocess.run
_ORIG_CHECK_OUTPUT = _real_subprocess.check_output
_ORIG_CALL = _real_subprocess.call
_ORIG_CHECK_CALL = _real_subprocess.check_call
_ORIG_POPEN = _real_subprocess.Popen


def _patched_run(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return _fake_completed_process(argv)
    return _ORIG_RUN(argv, *args, **kwargs)


def _patched_check_output(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return _fake_completed_process(argv).stdout
    return _ORIG_CHECK_OUTPUT(argv, *args, **kwargs)


def _patched_call(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return 0
    return _ORIG_CALL(argv, *args, **kwargs)


def _patched_check_call(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return 0
    return _ORIG_CHECK_CALL(argv, *args, **kwargs)


class _FakePopen:
    def __init__(self, argv, *args, **kwargs):
        self._proc = _fake_completed_process(argv)
        self.returncode = self._proc.returncode

    def communicate(self, *args, **kwargs):
        return self._proc.stdout, self._proc.stderr

    def wait(self, *args, **kwargs):
        return self.returncode


def _patched_popen(argv, *args, **kwargs):
    if _is_acp_money_argv(argv):
        return _FakePopen(argv, *args, **kwargs)
    return _ORIG_POPEN(argv, *args, **kwargs)


def install_subprocess_patch() -> None:
    """Monkeypatch subprocess.{run,check_output,call,check_call,Popen} so a
    duty.py that shells `acp trade`/`acp wallet send-transaction`/`acp card
    issue` gets recorded here instead of spawning the real CLI. Everything
    else passes through unmodified. Idempotent — replay.py may call this more
    than once."""
    _real_subprocess.run = _patched_run
    _real_subprocess.check_output = _patched_check_output
    _real_subprocess.call = _patched_call
    _real_subprocess.check_call = _patched_check_call
    _real_subprocess.Popen = _patched_popen


install_subprocess_patch()
