"""Cross-contamination test: one wallet present on BOTH exchanges must stay
fully separated across profiles, lists, hiding, and telegram follower lookup.

Standalone asyncio runner (dev DB, real code paths). Seeds its own rows and
deletes exactly those rows at the end.
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings                            # noqa: E402
from app.db.database import init_db, get_session_factory   # noqa: E402

WALLET = "0x7e57000000000000000000000000000000000001"   # test-only address
FOLLOWER = "0x7e57000000000000000000000000000000000002"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((PASS if ok else FAIL, name))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


async def main():
    await init_db(settings.DATABASE_URL)
    from sqlalchemy import text
    from app.services.copy import trader_profiles, watchlist as wl_service
    from app.services import telegram_bot
    from app.routers.leaders import _load_hl_leaderboard

    sf = get_session_factory()

    # --- seed: same wallet, both exchanges, different stats/names ---
    p_perpl = await trader_profiles.upsert_profile(WALLET, display_name="PerplIdentity", exchange="perpl")
    p_hl = await trader_profiles.upsert_profile(WALLET, display_name="HlIdentity", exchange="hl")
    now = datetime.utcnow()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO leaderboard_snapshots (exchange, wallet_address, rank, pnl_total, roi, volume, period, timestamp) "
            "VALUES ('hl', :w, 1, 111111, 11.1, 999999999, 'all', :t), "
            "('perpl', :w, 1, 222222, 22.2, 888888888, 'all', :t)"), {"w": WALLET, "t": now})
        await s.commit()

    try:
        # 1. profile endpoints resolve per-exchange (no merge)
        check("profile perpl keeps its own display_name",
              p_perpl["display_name"] == "PerplIdentity" and p_perpl["exchange"] == "perpl")
        check("profile hl keeps its own display_name",
              p_hl["display_name"] == "HlIdentity" and p_hl["exchange"] == "hl")
        g1 = await trader_profiles.get_profile(WALLET, exchange="perpl")
        g2 = await trader_profiles.get_profile(WALLET, exchange="hl")
        check("get_profile returns distinct rows per exchange",
              g1["display_name"] == "PerplIdentity" and g2["display_name"] == "HlIdentity")

        # 2. HL list loader serves only exchange='hl' snapshot data
        hl_rows = await _load_hl_leaderboard("all", "pnl")
        mine = [r for r in hl_rows if r["wallet_address"] == WALLET]
        check("hl list contains the wallet with HL stats only",
              len(mine) == 1 and mine[0]["pnl_total"] == 111111 and mine[0]["exchange"] == "hl",
              f"got {mine[0]['pnl_total'] if mine else 'absent'}")

        # 3. hide on HL does not hide on Perpl
        async with sf() as s:
            await s.execute(text(
                "UPDATE trader_profiles SET is_hidden=1, hidden_at=NOW() "
                "WHERE wallet_address=:w AND exchange='hl'"), {"w": WALLET})
            await s.commit()
        hidden_hl = await trader_profiles.hidden_wallets("hl")
        hidden_perpl = await trader_profiles.hidden_wallets("perpl")
        check("hidden on hl", WALLET in hidden_hl)
        check("NOT hidden on perpl", WALLET not in hidden_perpl)
        hl_rows2 = await _load_hl_leaderboard("all", "pnl")
        check("hidden wallet vanishes from hl list",
              not any(r["wallet_address"] == WALLET for r in hl_rows2))
        g1b = await trader_profiles.get_profile(WALLET, exchange="perpl")
        check("perpl profile still visible after hl hide", not g1b["is_hidden"])

        # 4. watchlist + telegram follower lookup are exchange-scoped
        await wl_service.add_watch(FOLLOWER, WALLET, exchange="perpl")
        # link follower to a fake telegram chat id
        async with sf() as s:
            await s.execute(text(
                "INSERT INTO users (wallet_address, created_at) "
                "SELECT :f, NOW() FROM DUAL WHERE NOT EXISTS "
                "(SELECT 1 FROM users WHERE wallet_address=:f)"), {"f": FOLLOWER})
            uid = (await s.execute(text(
                "SELECT id FROM users WHERE wallet_address=:f"), {"f": FOLLOWER})).scalar_one()
            await s.execute(text(
                "INSERT INTO telegram_links (user_id, chat_id, is_active, linked_at) "
                "VALUES (:u, '777000777', 1, NOW())"), {"u": uid})
            await s.commit()
        f_perpl = await telegram_bot._get_followers_with_telegram(WALLET, exchange="perpl")
        f_hl = await telegram_bot._get_followers_with_telegram(WALLET, exchange="hl")
        check("perpl watch -> perpl alert followers include chat", "777000777" in f_perpl)
        check("perpl watch -> NO hl alert followers", "777000777" not in f_hl,
              f"hl followers: {f_hl}")
    finally:
        # cleanup exactly what we created
        async with sf() as s:
            await s.execute(text("DELETE FROM watchlists WHERE follower_wallet=:f"), {"f": FOLLOWER})
            await s.execute(text(
                "DELETE FROM telegram_links WHERE chat_id='777000777' AND user_id IN "
                "(SELECT id FROM users WHERE wallet_address=:f)"), {"f": FOLLOWER})
            await s.execute(text("DELETE FROM users WHERE wallet_address=:f"), {"f": FOLLOWER})
            await s.execute(text("DELETE FROM trader_profiles WHERE wallet_address=:w"), {"w": WALLET})
            await s.execute(text("DELETE FROM leaderboard_snapshots WHERE wallet_address=:w"), {"w": WALLET})
            await s.commit()

    fails = [n for st, n in results if st == FAIL]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
    if fails:
        sys.exit(1)
    print("EXCHANGE SEPARATION VERIFIED")


if __name__ == "__main__":
    asyncio.run(main())
