"""Live side inside the API process: new closed round trips of qualified
wallets are pushed to the `featured.trades` websocket channel within seconds
of the trades feed writing them (scripts/hl_trades_feed.py closes trips on
flat and inserts the row; this loop only watches round_trips.id)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger
from app.ws.client_feed import client_manager

logger = get_logger(__name__)

CHANNEL = "featured.trades"
POLL_SEC = 2.0
_task: asyncio.Task | None = None
_last_id: int | None = None
_qualified: dict[str, dict] = {}
_qualified_at = 0.0


async def qualified_wallets(max_age: float = 60.0) -> dict[str, dict]:
    """{wallet: {label, venue, display_name, one_big_win}} for the latest
    qualified rows, cached max_age seconds."""
    global _qualified, _qualified_at
    import time
    if time.monotonic() - _qualified_at < max_age and _qualified:
        return _qualified
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT f.venue, f.wallet, f.label, f.display_name, f.one_big_win, f.inactive FROM featured_wallets f JOIN ("
            " SELECT venue, wallet, MAX(as_of) mx FROM featured_wallets GROUP BY venue, wallet"
            ") l ON l.venue=f.venue AND l.wallet=f.wallet AND l.mx=f.as_of WHERE f.qualified=1"))).all()
    _qualified = {f"{v}:{w}": {"venue": v, "wallet": w, "label": l, "display_name": d, "one_big_win": bool(o), "inactive": bool(i)}
                  for v, w, l, d, o, i in rows}
    _qualified_at = time.monotonic()
    return _qualified


def trade_row(r: dict, q: dict | None) -> dict:
    return {
        "id": r["id"], "venue": r["venue"], "wallet": r["wallet"], "coin": r["coin"], "side": r["side"],
        "label": (q or {}).get("label"), "display_name": (q or {}).get("display_name"),
        "one_big_win": (q or {}).get("one_big_win", False),
        "open_time": r["open_time"], "close_time": r["close_time"], "hold_sec": r["hold_sec"],
        "pnl_usd": r["pnl_usd"], "pnl_pct_margin": r["pnl_pct_margin"], "ret_pct": r["ret_pct"],
        "avg_entry": r["avg_entry"], "avg_exit": r["avg_exit"], "n_fills": r["n_fills"], "n_adds": r["n_adds"],
        "n_closes": r["n_closes"], "max_size_usd": r["max_size_usd"], "leverage": r["leverage"],
        "entry_taker": r["entry_taker"], "averaged_down": r["averaged_down"], "added_to_winner": r["added_to_winner"],
        "fees": r["fees"], "funding": r["funding"], "liquidation": r["liquidation"],
        "entry_vs_24h_range": r.get("entry_vs_24h_range"), "smi_at_entry": r.get("smi_at_entry"),
    }


async def _new_rows(since_id: int) -> list[dict]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, venue, wallet, coin, side, open_time, close_time, n_fills, n_adds, n_closes, max_size_usd, avg_entry, avg_exit, "
            "leverage, pnl_usd, pnl_pct_margin, ret_pct, fees, funding, entry_taker, averaged_down, added_to_winner, liquidation, "
            "entry_vs_24h_range, smi_at_entry FROM round_trips WHERE id > :i ORDER BY id LIMIT 200"), {"i": since_id})).all()
    out = []
    for r in rows:
        ot, ct = r[5], r[6]
        out.append({"id": r[0], "venue": r[1], "wallet": r[2], "coin": r[3], "side": r[4],
                    "open_time": ot.isoformat(timespec="milliseconds"), "close_time": ct.isoformat(timespec="milliseconds"),
                    "hold_sec": int((ct - ot).total_seconds()), "n_fills": r[7], "n_adds": r[8], "n_closes": r[9],
                    "max_size_usd": float(r[10]), "avg_entry": float(r[11]), "avg_exit": float(r[12]),
                    "leverage": (float(r[13]) if r[13] is not None else None), "pnl_usd": float(r[14]),
                    "pnl_pct_margin": (float(r[15]) if r[15] is not None else None), "ret_pct": float(r[16]),
                    "fees": float(r[17]), "funding": float(r[18]), "entry_taker": (None if r[19] is None else bool(r[19])),
                    "averaged_down": bool(r[20]), "added_to_winner": bool(r[21]), "liquidation": bool(r[22]),
                    "entry_vs_24h_range": (float(r[23]) if r[23] is not None else None),
                    "smi_at_entry": (float(r[24]) if r[24] is not None else None)})
    return out


async def _loop(stop: asyncio.Event) -> None:
    global _last_id
    sf = get_session_factory()
    async with sf() as s:
        _last_id = int((await s.execute(text("SELECT COALESCE(MAX(id),0) FROM round_trips"))).scalar() or 0)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SEC)
            break
        except asyncio.TimeoutError:
            pass
        try:
            rows = await _new_rows(_last_id)
            if not rows:
                continue
            _last_id = max(r["id"] for r in rows)
            q = await qualified_wallets()
            now = datetime.utcnow()
            for r in rows:
                key = f"{r['venue']}:{r['wallet']}"
                if key not in q:
                    continue
                # the daily rebuild re-inserts old trips with new ids only on
                # a first build; only trips closed in the last hour are "new"
                try:
                    ct = datetime.fromisoformat(r["close_time"])
                except ValueError:
                    continue
                if (now - ct).total_seconds() > 3600:
                    continue
                await client_manager.broadcast(CHANNEL, {"type": "closed", "trade": trade_row(r, q[key])})
        except Exception as exc:
            logger.warning("featured live loop: %s", exc)


def start(stop: asyncio.Event) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop(stop))
        logger.info("featured live push started (%s)", CHANNEL)


async def stop_task() -> None:
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
