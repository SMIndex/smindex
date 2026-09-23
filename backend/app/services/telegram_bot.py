"""Telegram bot for copy trade notifications and trading.

Runs as an asyncio polling task inside the FastAPI process.
Users link via /start {code}, receive alerts when leaders trade,
and can reply to execute trades.
"""
import os
import re
from datetime import datetime

from sqlalchemy import select
from app.config import settings
from app.db.database import get_session_factory
from app.db.models import TelegramLink, User, WalletFollow
from app.services import telegram_queue
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Deep-link base for alert buttons — env-driven, not hardcoded, so a domain
# move (or a second deployment) doesn't ship stale links.
TERMINAL_URL = os.environ.get("PERPL_TERMINAL_URL", "https://smindex.xyz").rstrip("/")

# Will be None if python-telegram-bot not installed or no token
_app = None
_running = False


async def start():
    """Initialize and start the Telegram bot. Called from main.py lifespan."""
    global _app, _running

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.info("Telegram bot token not configured, skipping")
        return

    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    except ImportError:
        logger.warning("python-telegram-bot not installed, skipping Telegram bot")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", _handle_start))
    app.add_handler(CommandHandler("status", _handle_status))
    app.add_handler(CommandHandler("help", _handle_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    _app = app
    _running = True
    # All alert sends flow through the queue worker (throttle/retry/dedupe) —
    # detection loops only enqueue and never block on Telegram I/O.
    telegram_queue.start(_send_raw)
    logger.info("Telegram bot started (send queue active)")


async def stop():
    """Stop the Telegram bot."""
    global _app, _running
    await telegram_queue.stop()
    if _app and _running:
        await _app.updater.stop()
        await _app.stop()
        await _app.shutdown()
        _running = False
        logger.info("Telegram bot stopped")


async def _send_raw(chat_id: str, message: str, buttons: list | None = None):
    """Actually send one Telegram message. RAISES on failure — the queue
    worker owns retry/backoff/flood-control handling; swallowing errors here
    (the old behavior) hid every delivery failure forever.

    buttons: optional list of rows, each row a list of (text, url) tuples,
    rendered as an inline keyboard. Link previews are always disabled so the
    alert isn't dwarfed by a website preview card."""
    if not _app:
        raise RuntimeError("telegram bot not running")
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
    reply_markup = None
    if buttons:
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text=t, url=u) for t, u in row] for row in buttons]
        )
    await _app.bot.send_message(
        chat_id=int(chat_id), text=message, parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=reply_markup,
    )


async def send_notification(chat_id: str, message: str, buttons: list | None = None):
    """Direct-send wrapper kept for non-alert callers (e.g. link confirmation
    replies). Alert fan-out uses telegram_queue.enqueue instead."""
    if not _app:
        return
    try:
        await _send_raw(chat_id, message, buttons)
    except Exception as exc:
        logger.error("Failed to send Telegram notification to %s: %s", chat_id, exc)


def _trade_buttons(leader_wallet: str, exchange: str = "perpl") -> list:
    # deep link carries the exchange — an HL alert must open the HL-scoped profile
    suffix = f"?exchange={exchange}" if exchange != "perpl" else ""
    return [[
        ("📊 Open SMINDEX", f"{TERMINAL_URL}/trade"),
        ("👤 View trader", f"{TERMINAL_URL}/copy/trader/{leader_wallet.lower()}{suffix}"),
    ]]


async def _get_leader_name(wallet: str, exchange: str) -> str | None:
    """Display name for the alert header: HL leaders come from trader_profiles
    (leaderboard display names), Perpl leaders from the users table."""
    if exchange == "hl":
        try:
            from app.services.copy import trader_profiles
            prof = await trader_profiles.get_profile(wallet, exchange="hl")
            return (prof or {}).get("display_name")
        except Exception:
            return None
    return await _get_username(wallet)


def _fmt_usd(v: float, signed: bool = False) -> str:
    sign = "-" if v < 0 else ("+" if signed else "")
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:,.1f}M"
    if a >= 10_000:
        return f"{sign}${a / 1_000:,.1f}K"
    return f"{sign}${a:,.2f}"


def _short(wallet: str) -> str:
    return wallet[:6] + "…" + wallet[-4:]


def _alert_v2(*, symbol: str, side: str, venue: str, leader_wallet: str,
              leader_name: str | None, action: str, size: float, price: float,
              size_label: str | None = None, pnl: float | None = None,
              post_position: float | None = None,
              fills_count: int = 1) -> str:
    """Template v2 (PROFILE_QUALITY_REPORT D): compact, one fact per line."""
    side_emoji = "🟢" if side.lower() == "long" else "🔴"
    name_part = f"{leader_name} (<code>{_short(leader_wallet)}</code>)" if leader_name \
        else f"<code>{_short(leader_wallet)}</code>"
    merged = f" ({fills_count} fills merged)" if fills_count > 1 else ""
    lines = [
        f"{side_emoji} <b>{side.upper()} {symbol}</b> · {venue}",
        f"Leader: {name_part}",
        f"Action: {action}{merged}",
    ]
    if size and price:
        label = f" [{size_label}]" if size_label else ""
        lines.append(f"Fill: <code>{_fmt_qty(size)} {symbol}</code> ({_fmt_usd(size * price)}) @ <code>${price:,.6g}</code>{label}")
    elif price:
        lines.append(f"Price: <code>${price:,.6g}</code>")
    if pnl is not None:
        lines.append(f"PnL: <b>{_fmt_usd(pnl, signed=True)}</b>")
    if post_position is not None:
        if post_position == 0:
            lines.append("Position now: FLAT")
        else:
            pside = "LONG" if post_position > 0 else "SHORT"
            lines.append(f"Position now: {pside} <code>{_fmt_qty(abs(post_position))} {symbol}</code>")
    lines.append(f"{datetime.utcnow():%d %b %Y, %H:%M} UTC")
    return "\n".join(lines)


async def _get_username(wallet: str) -> str | None:
    """Look up username for a wallet address."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(User.username).where(User.wallet_address == wallet.lower())
        )
        row = result.first()
        return row[0] if row and row[0] else None


def _display_name(wallet: str, username: str | None) -> str:
    short = wallet[:6] + "..." + wallet[-4:]
    return f"{username} ({short})" if username else short


def _trader_line(wallet: str, username: str | None) -> str:
    """Trader identity for alerts. Full address in <code> so a tap copies it."""
    addr = f"<code>{wallet.lower()}</code>"
    return f"👤 <b>{username}</b>\n{addr}" if username else f"👤 {addr}"


def _fmt_qty(v: float) -> str:
    """Format a position size without scientific notation (65154 -> '65,154')."""
    if abs(v) >= 1000:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,.6g}"


async def notify_leader_entry(
    leader_wallet: str, market_id: int, symbol: str, side: str,
    entry_price: float, size: float,
    leverage: float = 0, notional: float = 0,
    mark_price: float | None = None,
    venue: str | None = None,
    exchange: str = "perpl",
    size_label: str = "Size",
    fills_count: int = 1,
    action: str | None = None,
    post_position: float | None = None,
):
    """Notify all followers when a leader opens a position (template v2).

    Honesty rules: leverage line only when actually known; notional derived
    from size*price; UTC timestamp; post-fill position only when derivable.
    """
    followers = await _get_followers_with_telegram(leader_wallet, exchange=exchange)
    if not followers:
        return

    leader_name = await _get_leader_name(leader_wallet, exchange)
    name = _display_name(leader_wallet, leader_name)

    msg = _alert_v2(
        symbol=symbol, side=side,
        venue=venue or ("Hyperliquid" if exchange == "hl" else "Perpl"),
        leader_wallet=leader_wallet, leader_name=leader_name,
        action=action or "Opened",
        size=size, price=entry_price,
        size_label=None if size_label in ("Size", "Fill size") else size_label,
        post_position=post_position, fills_count=fills_count,
    )
    if leverage:
        msg += f"\nLeverage: <code>{leverage:g}x</code>"

    buttons = _trade_buttons(leader_wallet, exchange)
    queued = 0
    for chat_id in followers:
        # Dedupe: same leader+market+side+price+size to the same chat within
        # the queue's window fires once (re-detection storms, restarts).
        key = f"entry:{leader_wallet.lower()}:{market_id}:{side}:{entry_price:.10g}:{size:.10g}:{chat_id}"
        if telegram_queue.enqueue(chat_id, msg, buttons=buttons, dedupe_key=key):
            queued += 1

    logger.info("Telegram entry alert queued for %d/%d followers of %s", queued, len(followers), name)


async def notify_leader_exit(
    leader_wallet: str, market_id: int, symbol: str, side: str,
    exit_price: float, pnl: float | None = None,
    entry_price: float = 0, size: float = 0,
    venue: str | None = None,
    exchange: str = "perpl",
    size_label: str = "Size",
    price_label: str = "Exit price",
    note: str | None = None,
    fills_count: int = 1,
    action: str | None = None,
    post_position: float | None = None,
):
    """Notify followers when a leader closes a position (template v2).

    Honesty rules: pnl=None means UNKNOWN — the PnL line is omitted, never a
    fabricated $0. Flips are labeled as such via `action`."""
    followers = await _get_followers_with_telegram(leader_wallet, exchange=exchange)
    if not followers:
        return

    leader_name = await _get_leader_name(leader_wallet, exchange)
    name = _display_name(leader_wallet, leader_name)
    pnl_str = "n/a" if pnl is None else _fmt_usd(pnl, signed=True)

    default_action = "Closed"
    if price_label.startswith("Avg entry"):
        default_action = "Closed (price = avg entry; exit px unknown)"
    msg = _alert_v2(
        symbol=symbol, side=side,
        venue=venue or ("Hyperliquid" if exchange == "hl" else "Perpl"),
        leader_wallet=leader_wallet, leader_name=leader_name,
        action=action or default_action,
        size=size, price=exit_price,
        size_label=None if size_label in ("Size", "Fill size") else size_label,
        pnl=pnl, post_position=post_position, fills_count=fills_count,
    )
    if note and not action:
        msg += f"\n⚠️ {note}"

    buttons = _trade_buttons(leader_wallet, exchange)
    queued = 0
    for chat_id in followers:
        key = f"exit:{leader_wallet.lower()}:{market_id}:{side}:{exit_price:.10g}:{size:.10g}:{chat_id}"
        if telegram_queue.enqueue(chat_id, msg, buttons=buttons, dedupe_key=key):
            queued += 1

    logger.info("Telegram exit alert queued for %d/%d followers of %s (PnL: %s)", queued, len(followers), name, pnl_str)


async def notify_copy_queued(
    follower_wallet: str, leader_wallet: str,
    symbol: str, side: str, allocation_usd: float, max_leverage: float,
):
    """Notify a follower that an auto-copy was queued for them."""
    # Find this specific follower's telegram chat_id
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink.chat_id)
            .join(User, TelegramLink.user_id == User.id)
            .where(
                User.wallet_address == follower_wallet.lower(),
                TelegramLink.is_active == True,
                TelegramLink.chat_id.isnot(None),
            )
        )
        row = result.first()
    if not row:
        return

    chat_id = row[0]
    leader_username = await _get_username(leader_wallet)
    leader_name = _display_name(leader_wallet, leader_username)

    msg = (
        f"📋 <b>{side.upper()} {symbol}</b> — copy queued\n"
        f"Copying {_trader_line(leader_wallet, leader_username)}\n"
        f"\n"
        f"Allocation:  <code>${allocation_usd:,.0f}</code>\n"
        f"Max leverage:  <code>{max_leverage:g}x</code>\n"
        f"\n"
        f"Open the terminal to confirm."
    )

    telegram_queue.enqueue(
        chat_id, msg, buttons=_trade_buttons(leader_wallet),
        dedupe_key=f"copyq:{follower_wallet.lower()}:{leader_wallet.lower()}:{symbol}:{side}",
    )
    logger.info("Copy queued notification enqueued for %s", follower_wallet[:10])


async def _get_followers_with_telegram(leader_wallet: str, exchange: str = "perpl") -> list[str]:
    """Get chat_ids of followers who have Telegram linked.

    Followers come from v1 watchlists + active copy_subscriptions (current UI)
    plus legacy wallet_follows (pre-v1 rows). Exchange-scoped: watching a
    wallet on Perpl must not subscribe you to that wallet's HL trades (and
    vice versa). Legacy wallet_follows predate the exchange column and are
    Perpl-only rows."""
    from app.db.copy_models import Watchlist, CopySubscription

    lw = leader_wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        follower_wallets: set[str] = set()

        result = await session.execute(
            select(Watchlist.follower_wallet).where(
                Watchlist.trader_wallet == lw,
                Watchlist.exchange == exchange,
            )
        )
        follower_wallets.update(row[0].lower() for row in result.all())

        result = await session.execute(
            select(CopySubscription.follower_wallet).where(
                CopySubscription.trader_wallet == lw,
                CopySubscription.status == "active",
                CopySubscription.exchange == exchange,
            )
        )
        follower_wallets.update(row[0].lower() for row in result.all())

        if exchange == "perpl":
            result = await session.execute(
                select(WalletFollow.follower_wallet).where(
                    WalletFollow.leader_wallet == lw,
                    WalletFollow.is_active == True,
                )
            )
            follower_wallets.update(row[0].lower() for row in result.all())

        if not follower_wallets:
            return []

        result = await session.execute(
            select(TelegramLink.chat_id)
            .join(User, TelegramLink.user_id == User.id)
            .where(
                User.wallet_address.in_(follower_wallets),
                TelegramLink.is_active == True,
                TelegramLink.chat_id.isnot(None),
            )
        )
        return [row[0] for row in result.all()]


# --- Handlers ---

async def _handle_start(update, context):
    """Handle /start {link_code} — link Telegram to account."""
    args = context.args
    chat_id = str(update.message.chat_id)

    if not args:
        await update.message.reply_text(
            "Welcome to the SMINDEX Copy Trade Bot!\n\n"
            "To link your account:\n"
            "1. Go to Settings on the terminal\n"
            "2. Click 'Link Telegram'\n"
            "3. Use the link provided\n\n"
            "Commands:\n"
            "/status - Check link status\n"
            "/help - Show help"
        )
        return

    code = args[0]

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(
                TelegramLink.link_code == code,
                TelegramLink.is_active == False,
            )
        )
        link = result.scalar_one_or_none()

        if not link:
            await update.message.reply_text("Invalid or expired link code. Please generate a new one from Settings.")
            return

        if link.link_code_expires and link.link_code_expires < datetime.utcnow():
            await update.message.reply_text("Link code expired. Please generate a new one from Settings.")
            return

        link.chat_id = chat_id
        link.is_active = True
        link.linked_at = datetime.utcnow()
        link.link_code = None
        link.link_code_expires = None
        await session.commit()

    await update.message.reply_text(
        "Account linked successfully!\n\n"
        "You'll receive alerts when leaders you follow open or close trades.\n"
        "Reply to alerts to execute trades."
    )
    logger.info("Telegram linked: chat_id=%s", chat_id)


async def _handle_status(update, context):
    """Show link status."""
    chat_id = str(update.message.chat_id)
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.chat_id == chat_id, TelegramLink.is_active == True)
        )
        link = result.scalar_one_or_none()

    if link:
        await update.message.reply_text("Your Telegram is linked to your SMINDEX account.")
    else:
        await update.message.reply_text("Not linked. Go to Settings on the terminal to link.")


async def _handle_help(update, context):
    await update.message.reply_text(
        "<b>Perpl Copy Trade Bot</b>\n\n"
        "You'll receive alerts when leaders trade.\n\n"
        "<b>Commands:</b>\n"
        "Reply to an alert:\n"
        "<code>YES BTC 5x $50</code> - Copy trade (symbol, leverage, amount)\n"
        "<code>CLOSE BTC</code> - Close your copy position\n\n"
        "/status - Check link status\n"
        "/help - This message",
        parse_mode="HTML",
    )


async def _handle_message(update, context):
    """Parse trade commands from user messages."""
    chat_id = str(update.message.chat_id)
    text = update.message.text.strip()

    # Look up user
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink, User)
            .join(User, TelegramLink.user_id == User.id)
            .where(TelegramLink.chat_id == chat_id, TelegramLink.is_active == True)
        )
        row = result.first()

    if not row:
        await update.message.reply_text("Account not linked. Use /start to link.")
        return

    link, user = row

    # Parse YES {symbol} {leverage}x ${amount}
    yes_match = re.match(r'^YES\s+(\w+)\s+(\d+)x?\s+\$?(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if yes_match:
        symbol = yes_match.group(1).upper()
        leverage = int(yes_match.group(2))
        amount = float(yes_match.group(3))
        await update.message.reply_text(
            f"Copy trade queued:\n"
            f"{symbol} | {leverage}x | ${amount:.0f}\n\n"
            f"Open the terminal to confirm execution.\n"
            f"(Server-side execution coming soon)"
        )
        logger.info("Telegram trade request: user=%s %s %dx $%.0f", user.wallet_address[:10], symbol, leverage, amount)
        return

    # Parse CLOSE {symbol}
    close_match = re.match(r'^CLOSE\s+(\w+)', text, re.IGNORECASE)
    if close_match:
        symbol = close_match.group(1).upper()
        await update.message.reply_text(
            f"Close request for {symbol} noted.\n\n"
            f"Open the terminal to close your position.\n"
            f"(Server-side execution coming soon)"
        )
        logger.info("Telegram close request: user=%s %s", user.wallet_address[:10], symbol)
        return

    await update.message.reply_text("Unrecognized command. Type /help for usage.")


async def notify_wallet(wallet: str, message: str) -> bool:
    """Best-effort plain HTML notification to a wallet's linked telegram chat.
    Returns False (never raises) when the wallet has no active link."""
    try:
        sf = get_session_factory()
        async with sf() as session:
            row = (await session.execute(
                select(TelegramLink.chat_id)
                .join(User, TelegramLink.user_id == User.id)
                .where(
                    User.wallet_address == wallet.lower(),
                    TelegramLink.is_active == True,  # noqa: E712
                    TelegramLink.chat_id.isnot(None),
                )
            )).first()
        if not row:
            return False
        await send_notification(row[0], message)
        return True
    except Exception:
        logger.exception("notify_wallet failed for %s", wallet[:10])
        return False
