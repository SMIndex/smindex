"""Platform ledger: every order and fill that goes through SMINDEX's own
trading proxy, recorded on the server as it happens (2026-10-12).

Why: the stats page summed `trade_history`, which is filled by each browser
POSTing its own trades after the fact - one row in ten days while hundreds of
orders went through. Perpl's own fill records cannot replace it either: they
are only readable with the user's own signed key, and they cover orders placed
anywhere on Perpl, not just here.

How an order is attributed to SMINDEX: the proxy remembers the `rq` of every
mt:22 frame a browser sends through it; the venue's order row (mt:24) for that
`rq` gives the `oid`; a fill (mt:25) counts when its `oid` is one of those.
Fills from Perpl's own site, or from the auto-copy engine on the same account,
carry other oids and are ignored (auto-copy is counted from auto_copy_log).

Strictly passive. `Session.on_client_frame` / `on_venue_frame` only parse and
`put_nowait` onto a bounded queue; one background task does the DB writes.
Nothing here may raise into, block, or alter the proxied traffic.
"""
import asyncio
import json
import time
from datetime import datetime

from sqlalchemy import text

from app.utils.logger import get_logger

logger = get_logger(__name__)

DDL = [
    "CREATE TABLE IF NOT EXISTS platform_orders ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY, wallet VARCHAR(42) NOT NULL, account_id BIGINT NULL,"
    " oid BIGINT NOT NULL, rq BIGINT NULL, market_id INT NULL, order_type INT NULL,"
    " status INT NULL, reject_reason INT NULL, is_trigger TINYINT(1) NOT NULL DEFAULT 0,"
    " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,"
    " UNIQUE KEY uq_po (wallet, oid), KEY ix_po_created (created_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS platform_fills ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY, wallet VARCHAR(42) NOT NULL, account_id BIGINT NULL,"
    " oid BIGINT NOT NULL, market_id INT NOT NULL, symbol VARCHAR(20) NULL, fill_type INT NULL,"
    " liquidity INT NULL, price_raw BIGINT NOT NULL, size_raw BIGINT NOT NULL,"
    " price DOUBLE NULL, size DOUBLE NULL, notional_usd DOUBLE NULL, fee_usd DOUBLE NULL,"
    " block BIGINT NOT NULL DEFAULT 0, tx_index INT NOT NULL DEFAULT 0, fill_ts DATETIME NOT NULL,"
    " created_at DATETIME NOT NULL,"
    " UNIQUE KEY uq_pf (wallet, oid, block, tx_index, price_raw, size_raw),"
    " KEY ix_pf_ts (fill_ts)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
]

QUEUE_MAX = 5000
SESSION_MAX = 2000            # rqs / oids remembered per browser connection
PENDING_TTL_SEC = 30.0        # a fill can arrive before its order row
_queue: asyncio.Queue | None = None
stats = {"orders": 0, "fills": 0, "dropped": 0, "unmatched": 0, "errors": 0}


def _put(item: tuple) -> None:
    if _queue is None:
        return
    try:
        _queue.put_nowait(item)
    except asyncio.QueueFull:
        stats["dropped"] += 1


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Session:
    """One browser connection through the trading proxy."""

    def __init__(self, wallet: str):
        self.wallet = (wallet or "").lower()
        self.rqs: set[int] = set()
        self.oids: set[int] = set()

    def on_client_frame(self, raw: str) -> None:
        """Browser -> Perpl. Remember the rq of each order frame."""
        if '"mt"' not in raw or "22" not in raw:
            return
        m = json.loads(raw)
        if not isinstance(m, dict) or m.get("mt") != 22:
            return
        rq = _int(m.get("rq"))
        if rq is not None and len(self.rqs) < SESSION_MAX:
            self.rqs.add(rq)

    def on_venue_frame(self, raw: str) -> None:
        """Perpl -> browser. Order rows for our rqs, and fills for our oids."""
        if '"mt"' not in raw:
            return
        head = raw[:40].replace(" ", "")
        if '"mt":24' not in head and '"mt":25' not in head:
            return
        m = json.loads(raw)
        rows = m.get("d") if isinstance(m, dict) else None
        if not isinstance(rows, list):
            return
        if m.get("mt") == 24:
            for r in rows:
                if not isinstance(r, dict):
                    continue
                oid, rq = _int(r.get("oid")), _int(r.get("rq"))
                if oid is None or (rq not in self.rqs and oid not in self.oids):
                    continue
                if len(self.oids) < SESSION_MAX:
                    self.oids.add(oid)
                _put(("order", self.wallet, {
                    "oid": oid, "rq": rq, "acc": _int(r.get("acc")), "mkt": _int(r.get("mkt")),
                    "t": _int(r.get("t")), "st": _int(r.get("st")), "sr": _int(r.get("sr")),
                    "trig": 1 if r.get("tp") else 0}))
        elif m.get("mt") == 25:
            for r in rows:
                if not isinstance(r, dict):
                    continue
                oid, mkt = _int(r.get("oid")), _int(r.get("mkt"))
                p, s = _int(r.get("p")), _int(r.get("s"))
                if None in (oid, mkt, p, s):
                    continue
                at = r.get("at") if isinstance(r.get("at"), dict) else {}
                _put(("fill", self.wallet, {
                    "oid": oid, "mkt": mkt, "acc": _int(r.get("acc")), "t": _int(r.get("t")),
                    "l": _int(r.get("l")), "p": p, "s": s, "f": _int(r.get("f")) or 0,
                    "b": _int(at.get("b")) or 0, "tx": _int(at.get("tx")) or 0,
                    "ts": _int(at.get("t")) or int(time.time() * 1000),
                    "known": oid in self.oids}, time.monotonic()))


async def _write_order(s, wallet: str, o: dict) -> None:
    now = datetime.utcnow()
    await s.execute(text(
        "INSERT INTO platform_orders (wallet, account_id, oid, rq, market_id, order_type, status,"
        " reject_reason, is_trigger, created_at, updated_at) VALUES (:w, :acc, :oid, :rq, :mkt, :t,"
        " :st, :sr, :trig, :now, :now) ON DUPLICATE KEY UPDATE status = VALUES(status),"
        " reject_reason = VALUES(reject_reason), updated_at = VALUES(updated_at)"),
        {"w": wallet, "now": now, **o})
    stats["orders"] += 1


async def _is_ours(s, wallet: str, oid: int) -> bool:
    return (await s.execute(text(
        "SELECT 1 FROM platform_orders WHERE wallet = :w AND oid = :oid LIMIT 1"),
        {"w": wallet, "oid": oid})).first() is not None


async def _write_fill(s, wallet: str, f: dict) -> None:
    from app.services import market_registry
    m = await market_registry.get_market_by_id(f["mkt"])
    price = size = notional = None
    symbol = None
    if m and m.get("price_decimals") is not None and m.get("size_decimals") is not None:
        price = f["p"] / 10 ** int(m["price_decimals"])
        size = f["s"] / 10 ** int(m["size_decimals"])
        notional = price * size
        symbol = m.get("symbol")
    await s.execute(text(
        "INSERT IGNORE INTO platform_fills (wallet, account_id, oid, market_id, symbol, fill_type,"
        " liquidity, price_raw, size_raw, price, size, notional_usd, fee_usd, block, tx_index,"
        " fill_ts, created_at) VALUES (:w, :acc, :oid, :mkt, :sym, :t, :l, :p, :s, :price, :size,"
        " :notional, :fee, :b, :tx, :fts, :now)"),
        {"w": wallet, "acc": f["acc"], "oid": f["oid"], "mkt": f["mkt"], "sym": symbol, "t": f["t"],
         "l": f["l"], "p": f["p"], "s": f["s"], "price": price, "size": size, "notional": notional,
         "fee": f["f"] / 1e6, "b": f["b"], "tx": f["tx"],
         "fts": datetime.utcfromtimestamp(f["ts"] / 1000), "now": datetime.utcnow()})
    stats["fills"] += 1


async def handle(sf, item: tuple, pending: list) -> None:
    async with sf() as s:
        if item[0] == "order":
            await _write_order(s, item[1], item[2])
        else:
            _, wallet, f, born = item
            if f["known"] or await _is_ours(s, wallet, f["oid"]):
                await _write_fill(s, wallet, f)
            elif time.monotonic() - born < PENDING_TTL_SEC:
                pending.append(item)          # its order row may not have landed yet
                return
            else:
                stats["unmatched"] += 1       # placed somewhere else: not ours to count
                return
        await s.commit()


async def _run(stop: asyncio.Event) -> None:
    from app.db.database import get_session_factory
    pending: list = []
    while not stop.is_set():
        try:
            item = await asyncio.wait_for(_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            item = None
        # Fills still waiting for their order row are retried only on the idle
        # tick, so a burst of new frames is never slowed by them.
        batch = [item] if item else pending
        if item is None:
            pending = []
        for it in batch:
            try:
                await handle(get_session_factory(), it, pending)
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("platform ledger write failed: %s", str(exc)[:200])


def start(stop: asyncio.Event) -> asyncio.Task:
    global _queue
    _queue = asyncio.Queue(maxsize=QUEUE_MAX)
    logger.info("platform ledger started (orders + fills through the trading proxy)")
    return asyncio.get_event_loop().create_task(_run(stop))
