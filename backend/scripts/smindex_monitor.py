#!/usr/bin/env python3
"""SMINDEX beta monitor (2026-10-07). Runs every minute from smindex-monitor.timer,
OUTSIDE the API process so it still reports when the API is down.

Alerts (to the Telegram chats linked to ADMIN_ADDRESSES wallets):
  uptime     https://smindex.xyz/ and /api/markets: alert on 2 failures in a row, and on recovery
  api        perpl-terminal restart loop (3+ starts within 15 min)
  mysql      MySQL restarted, or any kernel OOM kill
  sweep      no analytics sweep cycle logged for 45 min
  disk/swap  root disk over 85 %, swap over 90 % (and when they recover)
  autocopy   new auto-copy ERROR lines in the app log, new failed auto-copy decisions
  copy       new failed manual copy orders
Daily health summary at 08:00 IST (02:30 UTC): uptime, errors, sweep cycles, new
users, copy orders, auto-copy decisions over the last 24 h.

Flags: --dry (print instead of send), --selftest (one labelled test alert),
       --summary-now (send the daily summary now).
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ENV = "/var/www/terminal/backend/.env"
APP_LOG = "/var/log/perpl-terminal.log"
STATE_DIR = "/var/lib/smindex-monitor"
STATE = f"{STATE_DIR}/state.json"
CHECKS = {"site": "https://smindex.xyz/", "api": "https://smindex.xyz/api/markets"}
DRY = "--dry" in sys.argv


def env() -> dict:
    out = {}
    for line in open(ENV):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


E = env()


def db():
    import pymysql
    u = urllib.parse.urlparse(E["DATABASE_URL"].replace("mysql+aiomysql://", "mysql://"))
    return pymysql.connect(host=u.hostname or "127.0.0.1", port=u.port or 3306, user=urllib.parse.unquote(u.username or ""),
                           password=urllib.parse.unquote(u.password or ""), database=u.path.lstrip("/"), connect_timeout=8)


def admin_chats() -> list[str]:
    admins = [a.strip().lower() for a in E.get("ADMIN_ADDRESSES", "").split(",") if a.strip()]
    if not admins:
        return []
    con = db()
    try:
        with con.cursor() as c:
            c.execute("SELECT t.chat_id FROM telegram_links t JOIN users u ON u.id = t.user_id WHERE t.is_active = 1 "
                      "AND t.chat_id IS NOT NULL AND LOWER(u.wallet_address) IN (" + ",".join(["%s"] * len(admins)) + ")", admins)
            return [str(r[0]) for r in c.fetchall()]
    finally:
        con.close()


def send(text: str) -> None:
    text = f"<b>[SMINDEX monitor]</b> {text}"
    if DRY:
        print("WOULD SEND:", re.sub("<[^>]+>", "", text))
        return
    tok = E.get("TELEGRAM_BOT_TOKEN")
    for chat in admin_chats():
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15).read()
        except Exception as exc:
            print("telegram send failed:", type(exc).__name__)


def http_ok(url: str) -> tuple[bool, str]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "smindex-monitor"}), timeout=20)
        body = r.read()
        if r.status != 200:
            return False, f"HTTP {r.status}"
        if url.endswith("/api/markets"):
            try:
                if not json.loads(body).get("markets"):
                    return False, "no markets in response"
            except Exception:
                return False, "response is not JSON"
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


def unit(name: str) -> dict:
    out = subprocess.run(["systemctl", "show", name, "-p", "ActiveState,ActiveEnterTimestamp,NRestarts"],
                         capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)


def tail_new(path: str, st: dict, key: str) -> list[str]:
    """New lines since the last run (handles copytruncate rotation)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    off = st.get(key, size)            # first run: start at the end
    if size < off:
        off = 0
    lines = []
    if size > off:
        with open(path, "rb") as f:
            f.seek(off)
            lines = f.read(min(size - off, 20_000_000)).decode("utf-8", "replace").splitlines()
    st[key] = size
    return lines


def flag(st: dict, name: str, bad: bool, alert_msg: str, ok_msg: str) -> None:
    was = st.setdefault("flags", {}).get(name, False)
    if bad and not was:
        send(alert_msg)
    elif not bad and was:
        send(ok_msg)
    st["flags"][name] = bad


def run(st: dict) -> None:
    now = datetime.now(timezone.utc)
    # 9.1 uptime -------------------------------------------------------------
    hist = st.setdefault("uptime", [])
    all_ok = True
    for name, url in CHECKS.items():
        ok, why = http_ok(url)
        all_ok &= ok
        fails = st.setdefault("fails", {})
        if ok:
            if fails.get(name, 0) >= 2:
                send(f"✅ RECOVERED: {url} answers again (was down for {fails[name]} checks).")
            fails[name] = 0
        else:
            fails[name] = fails.get(name, 0) + 1
            if fails[name] == 2:
                send(f"🔴 DOWN: {url} failed 2 checks in a row ({why}).")
    hist.append([int(now.timestamp()), 1 if all_ok else 0])
    st["uptime"] = [h for h in hist if h[0] > now.timestamp() - 86400 * 2]

    # 9.2 API restart loop -----------------------------------------------------
    api = unit("perpl-terminal")
    starts = st.setdefault("api_starts", [])
    if api.get("ActiveEnterTimestamp") and api["ActiveEnterTimestamp"] != st.get("api_last_start"):
        if st.get("api_last_start"):
            starts.append(int(now.timestamp()))
        st["api_last_start"] = api["ActiveEnterTimestamp"]
    starts[:] = [t for t in starts if t > now.timestamp() - 900]
    flag(st, "api_loop", len(starts) >= 3 or api.get("ActiveState") not in ("active", "activating", "reloading"),
         f"🔴 perpl-terminal restart loop or down: {len(starts)} starts in 15 min, state {api.get('ActiveState')}.",
         "✅ perpl-terminal stable again.")
    # MySQL restart + OOM kills
    my = unit("mysql")
    if st.get("mysql_last_start") and my.get("ActiveEnterTimestamp") != st["mysql_last_start"]:
        send(f"🟠 MySQL restarted at {my.get('ActiveEnterTimestamp')} (state {my.get('ActiveState')}).")
    st["mysql_last_start"] = my.get("ActiveEnterTimestamp")
    since = st.get("oom_since") or (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    k = subprocess.run(["journalctl", "-k", "--since", since, "--no-pager", "-o", "cat"], capture_output=True, text=True).stdout
    for m in re.finditer(r"Killed process \d+ \(([^)]+)\).*?anon-rss:(\d+)kB", k):
        send(f"🔴 OOM kill: <code>{m.group(1)}</code> (anon-rss {int(m.group(2)) // 1024} MB).")
    st["oom_since"] = now.strftime("%Y-%m-%d %H:%M:%S")

    # app log: sweep freshness, auto-copy errors, error counts --------------------
    new = tail_new(APP_LOG, st, "log_off")
    for line in new:
        if "position sweep cycle" in line:
            st["last_sweep"] = line[:19]
            day = st.setdefault("sweeps", [])
            day.append(int(now.timestamp()))
        if "| ERROR" in line or "| CRITICAL" in line:
            st.setdefault("errors", []).append(int(now.timestamp()))
    st["sweeps"] = [t for t in st.get("sweeps", []) if t > now.timestamp() - 86400]
    st["errors"] = [t for t in st.get("errors", []) if t > now.timestamp() - 86400]
    ac_err = [line for line in new if "copy_auto" in line and ("| ERROR" in line or "| CRITICAL" in line)]
    if ac_err:
        send(f"🟠 Auto-copy errors: {len(ac_err)} new.\n<code>{ac_err[-1][:300]}</code>")
    last = st.get("last_sweep")
    if last:
        age = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() / 60
        flag(st, "sweep", age > 45, f"🟠 Analytics sweep stalled: last cycle {age:.0f} min ago ({last} UTC).",
             "✅ Analytics sweep running again.")

    # disk / swap ---------------------------------------------------------------
    du = shutil.disk_usage("/")
    disk = du.used / du.total * 100
    flag(st, "disk", disk > 85, f"🟠 Disk at {disk:.0f}% (over 85%).", f"✅ Disk back to {disk:.0f}%.")
    mem = dict(line.split(":", 1) for line in open("/proc/meminfo").read().splitlines())
    st_kb = lambda k: int(mem[k].split()[0])
    swap = 100 - st_kb("SwapFree") / max(st_kb("SwapTotal"), 1) * 100
    flag(st, "swap", swap > 90, f"🟠 Swap at {swap:.0f}% (over 90%).", f"✅ Swap back to {swap:.0f}%.")

    # copy failures (DB) ----------------------------------------------------------
    con = db()
    try:
        with con.cursor() as c:
            c.execute("SELECT COALESCE(MAX(id),0) FROM copy_orders")
            top = c.fetchone()[0]
            seen = st.get("copy_seen", top)
            c.execute("SELECT id, symbol, side, error_message FROM copy_orders WHERE id > %s AND status = 'failed' ORDER BY id", (seen,))
            for oid, sym, side, err in c.fetchall():
                send(f"🟠 Copy order #{oid} failed — {sym} {side}: {(err or 'no error text')[:200]}")
            st["copy_seen"] = top
            c.execute("SELECT COALESCE(MAX(id),0) FROM auto_copy_log")
            atop = c.fetchone()[0]
            aseen = st.get("auto_seen", atop)
            c.execute("SELECT COUNT(*) FROM auto_copy_log WHERE id > %s AND decision = 'failed'", (aseen,))
            n = c.fetchone()[0]
            if n:
                send(f"🟠 Auto-copy: {n} failed decision(s) since the last check.")
            st["auto_seen"] = atop
    finally:
        con.close()

    # 9.3 daily summary at 08:00 IST = 02:30 UTC ------------------------------------
    today = now.strftime("%Y-%m-%d")
    if "--summary-now" in sys.argv or (now.hour == 2 and now.minute >= 30 and st.get("summary_day") != today):
        send(summary(st, now))
        st["summary_day"] = today


def summary(st: dict, now: datetime) -> str:
    cut = now.timestamp() - 86400
    hist = [ok for t, ok in st.get("uptime", []) if t > cut]
    up = f"{sum(hist) / len(hist) * 100:.2f}% ({len(hist)} checks)" if hist else "no data yet"
    con = db()
    try:
        with con.cursor() as c:
            c.execute("SELECT COUNT(*) FROM users WHERE created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY")
            new_users = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users")
            users = c.fetchone()[0]
            c.execute("SELECT status, COUNT(*) FROM copy_orders WHERE created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY GROUP BY status")
            copy = ", ".join(f"{s} {n}" for s, n in c.fetchall()) or "none"
            c.execute("SELECT decision, COUNT(*) FROM auto_copy_log WHERE created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY GROUP BY decision")
            auto = ", ".join(f"{d} {n}" for d, n in c.fetchall()) or "none"
    finally:
        con.close()
    api = unit("perpl-terminal")
    return (f"☀️ <b>Daily health</b> {now:%Y-%m-%d %H:%M} UTC\n"
            f"Uptime 24h: {up}\nAPI up since: {api.get('ActiveEnterTimestamp')}\n"
            f"Errors logged 24h: {len(st.get('errors', []))}\nSweep cycles 24h: {len(st.get('sweeps', []))}\n"
            f"Users: {users} (+{new_users} in 24h)\nCopy orders 24h: {copy}\nAuto-copy decisions 24h: {auto}")


def main() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(f"{STATE_DIR}/lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                       # previous run still going
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if "--selftest" in sys.argv:
        send("🧪 Test alert: the beta monitor can reach this chat. No action needed.")
        return
    try:
        run(st)
    finally:
        tmp = STATE + ".tmp"
        json.dump(st, open(tmp, "w"))
        os.replace(tmp, STATE)


if __name__ == "__main__":
    main()
