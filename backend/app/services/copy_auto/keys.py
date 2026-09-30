"""Auto-copy Perpl API keys at rest (spec C1).

A user deposits a DEDICATED key (enrolled in their browser for auto-copy,
separate from their one-click key) after a consent screen. The opaque X-API-Key
token and the Ed25519 seed are Fernet-encrypted (auth_service key) in
auto_copy_keys. No endpoint ever returns them and nothing logs them.

Only executor.py may call load_secret(). Deleting the key pauses every one of
the user's auto-copy subscriptions in the same transaction.
"""
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.auth_service import decrypt_token, encrypt_token


async def store(user_id: int, wallet: str, api_key: str, seed_hex: str, pub_hex: str,
                scope: int, label: str) -> dict:
    seed_hex = seed_hex.lower().removeprefix("0x")
    if len(seed_hex) != 64 or any(c not in "0123456789abcdef" for c in seed_hex):
        raise ValueError("seed must be 32 bytes hex")
    if not api_key or len(api_key) > 512:
        raise ValueError("bad api key")
    if scope not in (2, 3):
        raise ValueError("auto-copy needs a key with trade scope")
    # the seed must actually produce the public key the user enrolled
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    pub = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex)).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw).hex()
    if pub != pub_hex.lower().removeprefix("0x"):
        raise ValueError("seed does not match the enrolled public key")
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO auto_copy_keys (user_id, wallet, api_key_enc, seed_enc, pub_hex, scope, label, created_at) "
            "VALUES (:u, :w, :k, :s, :p, :sc, :l, UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE "
            "wallet=VALUES(wallet), api_key_enc=VALUES(api_key_enc), seed_enc=VALUES(seed_enc), "
            "pub_hex=VALUES(pub_hex), scope=VALUES(scope), label=VALUES(label), created_at=VALUES(created_at), "
            "last_error=NULL"),
            {"u": user_id, "w": wallet.lower(), "k": encrypt_token(api_key), "s": encrypt_token(seed_hex),
             "p": "0x" + pub, "sc": scope, "l": label[:64]})
        await s.commit()
    return await status(user_id)


async def status(user_id: int) -> dict:
    """What the UI may know: presence, public key, scope, dates, last error. Never secrets."""
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT pub_hex, scope, label, created_at, last_used_at, last_error FROM auto_copy_keys "
            "WHERE user_id = :u"), {"u": user_id})).mappings().first()
    if not r:
        return {"present": False}
    return {"present": True, "public_key": r["pub_hex"], "scope": r["scope"], "label": r["label"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            "last_error": r["last_error"]}


async def delete(user_id: int) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = await s.execute(text("DELETE FROM auto_copy_keys WHERE user_id = :u"), {"u": user_id})
        p = await s.execute(text(
            "UPDATE auto_copy_subs SET status='paused', pause_reason='Perpl key deleted', updated_at=UTC_TIMESTAMP() "
            "WHERE user_id = :u AND status = 'active' AND mode = 'live'"), {"u": user_id})
        await s.commit()
    return {"deleted": bool(r.rowcount), "live_subscriptions_paused": p.rowcount}


async def load_secret(user_id: int) -> tuple[str, bytes] | None:
    """EXECUTOR ONLY. -> (api_key token, 32-byte seed) or None."""
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text("SELECT api_key_enc, seed_enc FROM auto_copy_keys WHERE user_id = :u"),
                             {"u": user_id})).first()
    if not r:
        return None
    return decrypt_token(r[0]), bytes.fromhex(decrypt_token(r[1]))


async def mark(user_id: int, *, used: bool = False, error: str | None = None) -> None:
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "UPDATE auto_copy_keys SET last_used_at = IF(:used, UTC_TIMESTAMP(), last_used_at), "
            "last_error = :e WHERE user_id = :u"), {"used": int(used), "e": (error or None) and error[:250], "u": user_id})
        await s.commit()
