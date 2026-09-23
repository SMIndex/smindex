"""Per-position build history (generic — every wallet, every position).

Derived from the SAME single `userFills` fetch the dating worker already
makes (profile.py::_dating_worker) — this module adds ZERO venue calls. The
derived histories live in an in-process cache keyed per wallet and refresh on
the profile-cache TTL (the worker runs at most once per fresh profile fetch).

DERIVATION (pure, unit-testable — see derive()):
  * The streak start is the SAME rule the dating logic uses: scanning the
    coin's fills newest→oldest, the current streak began at the most recent
    fill whose startPosition was flat or on the opposite side. Replay runs
    oldest→newest from there.
  * Running signed position uses the venue's own startPosition per fill (a
    per-fill integrity anchor, not our arithmetic); running size-weighted avg
    entry follows the venue's entryPx semantics: increasing fills reweight
    the average, reducing fills never touch it, a flip resets it to the
    crossing fill's price.
  * Actions: OPEN (from flat) / ADD (same-side increase) / REDUCE (same-side
    decrease < 50% of the position at that moment) / PARTIAL CLOSE (decrease
    >= 50% — documented display split; both are venue "Close X" fills) /
    FLIP (crosses zero).
  * Display aggregation: consecutive same-direction fills within
    MERGE_WINDOW_MS collapse into one entry (summed size, size-weighted
    price, fill count kept). Merges NEVER span a flat/flip boundary, and
    adds-only / reduces-only groups replay to EXACTLY the same running
    numbers as per-fill replay — aggregation cannot drift the math. Very
    long histories elide the middle with an honest marker entry.
  * Venue truth: the final derived running avg must reproduce the venue's
    entryPx within REL_TOL. If not (and the streak is complete), a
    `history_mismatch` warning logs BOTH values and the payload carries
    mismatch=True — the UI renders the venue number as authoritative. Never
    silently pick one.
  * Honest truncation: when the ~2000-fill window doesn't reach the streak
    start, `truncated=True`; the UI opens with "earlier fills unavailable —
    position dated to <date> via funding ledger", and every running value
    carries approx=True (the replay can't know the true average at the
    window edge). No mismatch warning in this state — divergence is
    expected, the venue number stays authoritative.
  * Streak integrity: if the fills-derived exact streak start disagrees with
    the persisted dating beyond the source's tolerance (fill: 60s; funding:
    26h — the old ledger aggregates daily; bound rows: any exact date wins),
    that IS a dating bug: derive() reports it, the worker logs
    `dating_mismatch` with both values and RE-DATES the cached row from the
    fills (the two sources are never left disagreeing).
"""
import time

from app.utils.logger import get_logger

logger = get_logger(__name__)

MERGE_WINDOW_MS = 60_000          # consecutive same-direction fills collapse
REL_TOL = 1e-3                    # derived avg vs venue entryPx tolerance.
                                  # The venue quotes entryPx to ~6 sig figs;
                                  # on a 4-sig-fig price one ulp is ~7.5e-4
                                  # (seen live: 0.001333 vs 0.001334), so a
                                  # tighter bound false-alarms on rounding.
MAX_ENTRIES = 60                  # display cap: first KEEP_HEAD + last rest
KEEP_HEAD = 8
DATING_TOL_SEC = {"fill": 60.0, "funding": 26 * 3600.0}

# wallet -> (monotonic_ts, {(coin, side_sign): history dict})
_cache: dict[str, tuple[float, dict]] = {}


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _signed(f: dict) -> float:
    sz = _f(f.get("sz"))
    return sz if f.get("side") == "B" else -sz


def _streak_fills(coin_fills: list[dict], sign: int) -> tuple[list[dict], bool]:
    """(fills of the current streak oldest→newest, truncated). Same start rule
    as profile._position_open_times: newest→oldest, the streak begins at the
    most recent fill whose startPosition was flat or opposite-signed."""
    newest_first = sorted(coin_fills, key=lambda f: f["time"], reverse=True)
    streak: list[dict] = []
    truncated = True
    for f in newest_first:
        streak.append(f)
        spos = _f(f.get("startPosition"))
        if spos == 0 or (spos > 0) != (sign > 0):
            truncated = False
            break
    streak.reverse()
    return streak, truncated


def _replay(streak: list[dict], truncated: bool) -> tuple[list[dict], float | None]:
    """Aggregate + replay. Returns (entries, exact final avg or None)."""
    # group consecutive same-direction fills within the merge window; never
    # merge across a flat/flip boundary (spos==0 or sign change of spos)
    groups: list[list[dict]] = []
    for f in streak:
        spos = _f(f.get("startPosition"))
        d = _signed(f)
        boundary = spos == 0 or (spos > 0) != ((spos + d) > 0) or (spos + d) == 0
        g = groups[-1] if groups else None
        if (g and not boundary
                and (_signed(g[-1]) > 0) == (d > 0)
                and f["time"] - g[-1]["time"] <= MERGE_WINDOW_MS
                # the previous group must not itself end on a boundary
                and _f(g[-1].get("startPosition")) != 0
                and (_f(g[-1].get("startPosition")) + _signed(g[-1])) != 0):
            g.append(f)
        else:
            groups.append([f])

    entries: list[dict] = []
    pos = _f(streak[0].get("startPosition")) if streak else 0.0
    avg: float | None = None
    approx = truncated                 # unknown pre-window average
    for g in groups:
        delta = sum(_signed(f) for f in g)
        gsz = sum(abs(_signed(f)) for f in g)
        px = (sum(abs(_signed(f)) * _f(f.get("px")) for f in g) / gsz) if gsz else 0.0
        prev = pos
        pos = _f(g[0].get("startPosition")) + delta   # venue-anchored, not drift
        if prev == 0:
            action = "OPEN"
            avg = px
        elif (prev > 0) != (pos > 0) and pos != 0:
            action = "FLIP"
            avg = px
            approx = False   # a flip resets the average — exact from here on
        elif pos == 0:
            action = "CLOSE"          # streak actually ended (data race) — stop
        elif abs(pos) > abs(prev):
            action = "ADD"
            if avg is None:
                avg = px              # truncated stream starting on an add
            else:
                avg = (avg * abs(prev) + px * gsz) / (abs(prev) + gsz)
        else:
            cut = (abs(prev) - abs(pos)) / abs(prev) if prev else 0.0
            action = "PARTIAL CLOSE" if cut >= 0.5 else "REDUCE"
            if avg is None:
                avg = px              # truncated stream starting on a reduce
        entries.append({
            "t": g[0]["time"] / 1000.0,
            "action": action,
            "size": round(gsz, 8),
            "px": round(px, 8),
            "run_size": round(abs(pos), 8),
            "run_avg": round(avg, 8) if avg is not None else None,
            "approx": approx,
            "fills": len(g),
        })
        if action == "CLOSE":
            break
    # a FLIP mid-history makes everything after it exact even when truncated
    return entries, (avg if entries else None)


def derive_one(coin_fills: list[dict], coin: str, side: str,
               venue_entry: float | None,
               dated: dict | None) -> dict | None:
    """History for one open position. None when the wallet has no fills for
    the coin at all (nothing honest to show — the UI omits the icon)."""
    sign = 1 if side == "long" else -1
    fills = [f for f in coin_fills if f.get("time") is not None]
    if not fills:
        return None
    streak, truncated = _streak_fills(fills, sign)
    if not streak:
        return None
    entries, derived = _replay(streak, truncated)
    if not entries:
        return None

    # mismatch is only claimable when the final average is EXACT (complete
    # streak, or a mid-stream flip reset it); an approx value is expected to
    # diverge and the venue number is authoritative anyway
    final_exact = not entries[-1]["approx"]
    mismatch = bool(final_exact and derived is not None and venue_entry
                    and abs(derived - venue_entry) / venue_entry > REL_TOL)

    opens = [e for e in entries if e["action"] in ("OPEN", "ADD", "FLIP")]
    total = len(entries)
    elided = 0
    if total > MAX_ENTRIES:
        elided = total - MAX_ENTRIES
        entries = entries[:KEEP_HEAD] + entries[KEEP_HEAD + elided:]

    return {
        "coin": coin, "side": side,
        "truncated": truncated,
        "dated": dated,                     # {"opened_at"/"opened_before", "source"}
        "entries": entries,
        "elided": elided,
        "adds_count": len(opens),
        "first_fill_px": opens[0]["px"] if opens else entries[0]["px"],
        "streak_start": None if truncated else entries[0]["t"],
        "venue_entry": venue_entry,
        "derived_entry": round(derived, 8) if derived is not None else None,
        "mismatch": mismatch,
    }


def derive(wallet: str, fills: list[dict], positions: list[dict],
           cache_rows: dict) -> tuple[dict, list[dict]]:
    """All open positions' histories + dating-mismatch reports.
    Returns ({(coin, sign): history}, [mismatch dicts])."""
    by_coin: dict[str, list[dict]] = {}
    for f in fills:
        by_coin.setdefault(str(f.get("coin", "")).upper(), []).append(f)

    out: dict = {}
    dating_bugs: list[dict] = []
    for p in positions:
        coin = p["coin"]
        sign = 1 if p["side"] == "long" else -1
        row = cache_rows.get((coin, sign)) or {}
        h = derive_one(by_coin.get(coin, []), coin, p["side"],
                       p.get("entry_px") or None,
                       {"opened_at": row.get("opened_at"),
                        "opened_before": row.get("opened_before"),
                        "source": row.get("source")} if row else None)
        if h is None:
            continue
        if h["mismatch"]:
            logger.warning(
                "history_mismatch %s %s %s: derived avg %s vs venue entryPx %s "
                "(venue rendered as authoritative)", wallet[:10], coin, p["side"],
                h["derived_entry"], h["venue_entry"])
        # streak integrity vs persisted dating (module doc: never left disagreeing)
        if row:
            src = row.get("source")
            cached_ts = row.get("opened_at")
            tol = DATING_TOL_SEC.get(src or "", 0.0)
            if h["streak_start"] is not None:
                # exact fills-derived start available
                disagree = (
                    (cached_ts is not None and abs(cached_ts - h["streak_start"]) > tol)
                    or (cached_ts is None and src == "bound"))
                if disagree:
                    dating_bugs.append({
                        "kind": "exact", "coin": coin, "sign": sign,
                        "cached": cached_ts, "cached_source": src,
                        "fills_start": h["streak_start"]})
            elif h["truncated"] and cached_ts is not None and h["entries"]:
                # no exact start, but the oldest visible fill PROVES the
                # position was already open then — a cached "opened_at" later
                # than that (beyond the source's granularity) is refuted
                first_t = h["entries"][0]["t"]
                if cached_ts > first_t + tol:
                    dating_bugs.append({
                        "kind": "bound", "coin": coin, "sign": sign,
                        "cached": cached_ts, "cached_source": src,
                        "fills_start": first_t})
        out[(coin, sign)] = h
    return out, dating_bugs


def put(wallet: str, histories: dict) -> None:
    _cache[wallet.lower()] = (time.monotonic(), histories)


def get(wallet: str) -> dict | None:
    c = _cache.get(wallet.lower())
    return c[1] if c else None


def age_sec(wallet: str) -> float | None:
    c = _cache.get(wallet.lower())
    return time.monotonic() - c[0] if c else None


def is_stale(wallet: str, ttl: float) -> bool:
    a = age_sec(wallet)
    return a is None or a >= ttl
