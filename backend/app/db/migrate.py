"""Guarded startup DDL (owner, 2026-11-04): a migration statement runs only
when information_schema says the table, column, index or constraint it
creates is actually missing (or a column's type differs). When nothing is
missing the statement is not sent at all, so startup takes no metadata lock
on any table. Every statement that does run goes out with
lock_wait_timeout = 10; a lock it cannot get is logged as
"migration skipped: table locked" and startup goes on.

Background: on 2026-11-04 an OPTIMIZE TABLE leader_trade_events held a
metadata lock on copy_orders (foreign key), the unconditional
`ALTER TABLE copy_orders MODIFY COLUMN mode ...` that ran on every boot
waited behind it, systemd killed the hung start, and the API served nothing
for eight minutes.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.elements import TextClause

from app.utils.logger import get_logger

logger = get_logger(__name__)

LOCK_WAIT_SEC = 10
LOCK_ERRORS = {1205, 1206, 3572}       # lock wait timeout, total locks exceeded, nowait
stats = {"checked": 0, "applied": 0, "skipped_locked": 0, "nothing_to_do": 0}


def summary() -> str:
    return (f"{stats['checked']} statements checked, {stats['applied']} applied, {stats['nothing_to_do']} nothing to do, "
            f"{stats['skipped_locked']} skipped (table locked)")

_RE_CREATE = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?", re.I)
_RE_ALTER = re.compile(r"^\s*ALTER\s+TABLE\s+`?(\w+)`?\s+(.*)$", re.I | re.S)
_RE_CREATE_INDEX = re.compile(r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+`?(\w+)`?\s+ON\s+`?(\w+)`?", re.I)
_RE_ADD_COL = re.compile(r"^ADD\s+(?:COLUMN\s+)?`?(\w+)`?\s+(.+)$", re.I | re.S)
_RE_MODIFY = re.compile(r"^(?:MODIFY|CHANGE)\s+(?:COLUMN\s+)?`?(\w+)`?\s+(.+)$", re.I | re.S)
_RE_ADD_INDEX = re.compile(r"^ADD\s+(?:UNIQUE\s+)?(?:INDEX|KEY)\s+`?(\w+)`?", re.I)
_RE_ADD_CONSTRAINT = re.compile(r"^ADD\s+CONSTRAINT\s+`?(\w+)`?", re.I)
_RE_DROP_INDEX = re.compile(r"^DROP\s+(?:INDEX|KEY)\s+`?(\w+)`?", re.I)
_RE_DROP_COL = re.compile(r"^DROP\s+(?:COLUMN\s+)?`?(\w+)`?", re.I)


def _sql(stmt) -> str:
    return stmt.text if isinstance(stmt, TextClause) else str(stmt)


def _split_clauses(body: str) -> list[str]:
    """Top-level comma split of an ALTER body (commas inside parentheses and
    quotes stay)."""
    out, depth, cur, q = [], 0, [], None
    for ch in body:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in ("'", '"', "`"):
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    # trailing ALGORITHM= / LOCK= options are not clauses
    return [c for c in out if not re.match(r"^(ALGORITHM|LOCK)\s*=", c, re.I)]


async def _table_exists(ex, table: str) -> bool:
    n = (await ex.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = :t"), {"t": table})).scalar()
    return bool(n)


async def _column(ex, table: str, col: str) -> dict | None:
    row = (await ex.execute(text(
        "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"), {"t": table, "c": col})).first()
    if not row:
        return None
    return {"type": str(row[0]).lower(), "nullable": str(row[1]).upper() == "YES", "default": row[2]}


async def _index_exists(ex, table: str, name: str) -> bool:
    n = (await ex.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"),
        {"t": table, "i": name})).scalar()
    return bool(n)


async def _constraint_exists(ex, table: str, name: str) -> bool:
    n = (await ex.execute(text(
        "SELECT COUNT(*) FROM information_schema.table_constraints WHERE table_schema = DATABASE() AND table_name = :t AND constraint_name = :c"),
        {"t": table, "c": name})).scalar()
    return bool(n) or await _index_exists(ex, table, name)


def _type_matches(have: dict, spec: str) -> bool:
    """Column spec after the name, e.g. "VARCHAR(12) NOT NULL DEFAULT 'paper'":
    same type, same nullability, same default (when the spec states one)."""
    s = spec.strip()
    m = re.match(r"^([A-Za-z]+(?:\s*\([^)]*\))?(?:\s+UNSIGNED)?)", s)
    if not m:
        return False
    want_type = re.sub(r"\s+", "", m.group(1).lower())
    have_type = re.sub(r"\s+", "", have["type"])
    if want_type != have_type:
        # int(11) vs int, bigint(20) vs bigint: MySQL 8 drops the display width
        if re.sub(r"\(\d+\)$", "", want_type) != re.sub(r"\(\d+\)$", "", have_type) or want_type.startswith(("varchar", "char", "decimal")):
            return False
    want_nullable = not re.search(r"\bNOT\s+NULL\b", s, re.I)
    if want_nullable != have["nullable"]:
        return False
    d = re.search(r"\bDEFAULT\s+('(?:[^']*)'|\"(?:[^\"]*)\"|[^\s,]+)", s, re.I)
    if d:
        want = d.group(1).strip("'\"")
        have_d = have["default"]
        if want.upper() == "NULL":
            if have_d is not None:
                return False
        elif have_d is None or str(have_d).strip("'\"").lower() != want.lower():
            return False
    return True


async def _needed(ex, sql: str) -> tuple[bool, str]:
    """(run_it, reason)."""
    m = _RE_CREATE.match(sql)
    if m:
        t = m.group(1)
        return (not await _table_exists(ex, t)), f"table {t}"
    m = _RE_CREATE_INDEX.match(sql)
    if m:
        ix, t = m.group(1), m.group(2)
        return (not await _index_exists(ex, t, ix)), f"index {t}.{ix}"
    m = _RE_ALTER.match(sql)
    if m:
        t, body = m.group(1), m.group(2)
        if not await _table_exists(ex, t):
            return False, f"table {t} missing, nothing to alter"
        clauses = _split_clauses(body)
        if not clauses:
            return True, "alter"
        for c in clauses:
            mm = _RE_ADD_COL.match(c)
            if mm and not re.match(r"^ADD\s+(UNIQUE\s+)?(INDEX|KEY|CONSTRAINT|FOREIGN|PRIMARY)", c, re.I):
                if await _column(ex, t, mm.group(1)) is None:
                    return True, f"column {t}.{mm.group(1)}"
                continue
            mm = _RE_MODIFY.match(c)
            if mm:
                have = await _column(ex, t, mm.group(1))
                if have is None or not _type_matches(have, mm.group(2)):
                    return True, f"column type {t}.{mm.group(1)}"
                continue
            mm = _RE_ADD_INDEX.match(c)
            if mm:
                if not await _index_exists(ex, t, mm.group(1)):
                    return True, f"index {t}.{mm.group(1)}"
                continue
            mm = _RE_ADD_CONSTRAINT.match(c)
            if mm:
                if not await _constraint_exists(ex, t, mm.group(1)):
                    return True, f"constraint {t}.{mm.group(1)}"
                continue
            mm = _RE_DROP_INDEX.match(c)
            if mm:
                if await _index_exists(ex, t, mm.group(1)):
                    return True, f"drop index {t}.{mm.group(1)}"
                continue
            mm = _RE_DROP_COL.match(c)
            if mm:
                if await _column(ex, t, mm.group(1)) is not None:
                    return True, f"drop column {t}.{mm.group(1)}"
                continue
            return True, "alter clause not recognised: " + c[:40]
        return False, f"{t} already as specified"
    return True, "not a guarded statement"


async def ddl(ex, stmt, params: dict | None = None) -> bool:
    """Run a DDL statement only when it changes something. `ex` is an
    AsyncSession or an AsyncConnection. Returns True when it ran. A lock it
    cannot get within LOCK_WAIT_SEC is logged and skipped, never raised."""
    sql = _sql(stmt)
    stats["checked"] += 1
    try:
        run, why = await _needed(ex, sql)
    except Exception as exc:  # noqa: BLE001: a failed check falls back to running the statement guarded by the timeout
        run, why = True, f"check failed ({exc})"
    if not run:
        stats["nothing_to_do"] += 1
        return False
    try:
        await ex.execute(text(f"SET SESSION lock_wait_timeout = {LOCK_WAIT_SEC}"))
        await ex.execute(text(sql) if not isinstance(stmt, TextClause) else stmt, params or {})
        stats["applied"] += 1
        logger.info("migration applied: %s", why)
        return True
    except OperationalError as exc:
        code = exc.orig.args[0] if getattr(exc, "orig", None) and exc.orig.args else None
        if code in LOCK_ERRORS:
            stats["skipped_locked"] += 1
            logger.warning("migration skipped: table locked (%s)", why)
            try:
                await ex.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False
        raise
