from datetime import datetime, timezone, timedelta

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import User
from app.services.auth_service import generate_siwe_payload, verify_jwt, encrypt_token, decrypt_token
from app.utils.logger import get_logger
from jose import jwt as jose_jwt
from siwe import SiweMessage
from app.config import settings

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"


class PayloadRequest(BaseModel):
    address: str


class ConnectRequest(BaseModel):
    message: str
    signature: str


class UserResponse(BaseModel):
    wallet_address: str
    perpl_linked: bool
    username: str | None = None


class ConnectResponse(BaseModel):
    token: str
    user: UserResponse


class LinkPerplRequest(BaseModel):
    perpl_token: str


async def get_authenticated_user(authorization: str = Header(...)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization[7:]
    try:
        payload = verify_jwt(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user


async def get_optional_user(authorization: str = Header(None)) -> Optional[User]:
    """Like get_authenticated_user but returns None if no/invalid token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    try:
        payload = verify_jwt(token)
    except (ValueError, Exception):
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == int(user_id)))
        return result.scalar_one_or_none()


# Issued app-login SIWE nonces -> expiry epoch. Single-use: consumed on /connect
# so a captured SIWE message can't be replayed. Single uvicorn worker, so an
# in-memory store is sufficient; a restart just forces a fresh sign-in.
_issued_nonces: dict[str, float] = {}
_NONCE_TTL = 600  # seconds


def _remember_nonce(nonce: str) -> None:
    import time as _t
    now = _t.time()
    # prune expired
    for n in [k for k, exp in _issued_nonces.items() if exp < now]:
        _issued_nonces.pop(n, None)
    _issued_nonces[nonce] = now + _NONCE_TTL


def _consume_nonce(nonce: str) -> bool:
    import time as _t
    exp = _issued_nonces.pop(nonce, None)
    return exp is not None and exp >= _t.time()


@router.post("/payload")
async def create_payload(req: PayloadRequest, request: Request) -> dict:
    # sign for the host the user is actually on (see generate_siwe_payload)
    payload = generate_siwe_payload(req.address, host=request.headers.get("host"))
    _remember_nonce(payload["nonce"])
    return payload


@router.post("/connect", response_model=ConnectResponse)
async def connect_wallet(req: ConnectRequest) -> ConnectResponse:
    try:
        siwe_msg = SiweMessage.from_message(req.message)
        siwe_msg.verify(req.signature)
    except Exception as exc:
        logger.warning("SIWE verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Signature verification failed")

    # Single-use nonce: reject a message whose nonce we didn't issue or that was
    # already consumed (replay protection).
    if not _consume_nonce(siwe_msg.nonce):
        raise HTTPException(status_code=401, detail="Invalid or expired sign-in nonce. Please try again.")

    wallet_address = siwe_msg.address.lower()
    now = datetime.now(timezone.utc)

    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.wallet_address == wallet_address)
        )
        user = result.scalar_one_or_none()

        if user:
            user.updated_at = now
            await session.commit()
        else:
            user = User(wallet_address=wallet_address, created_at=now, updated_at=now)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        token_payload = {
            "sub": str(user.id),
            "wallet": wallet_address,
            "exp": datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRY_HOURS),
            "iat": datetime.now(timezone.utc),
        }
        token = jose_jwt.encode(token_payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)

        user_resp = UserResponse(
            wallet_address=user.wallet_address,
            perpl_linked=user.perpl_auth_token_encrypted is not None,
            username=user.username,
        )
        return ConnectResponse(token=token, user=user_resp)


@router.post("/perpl-payload")
async def perpl_payload(req: PayloadRequest) -> dict:
    """Proxy: get SIWE payload from Perpl API."""
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{settings.PERPL_REST_URL}/api/v1/auth/payload",
            json={"chain_id": settings.PERPL_CHAIN_ID, "address": req.address},
        )
        if resp.status_code == 418:
            raise HTTPException(status_code=418, detail="Wallet not whitelisted on Perpl. Visit perpl.xyz to get access.")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Perpl auth error: {resp.text}")
        return resp.json()


class PerplConnectRequest(BaseModel):
    chain_id: int = 143
    address: str
    message: str
    nonce: str
    issued_at: int | str
    mac: str
    signature: str


# Store Perpl sessions: address -> {nonce, cookies}
# In-memory cache + DB persistence
_perpl_sessions: dict[str, dict] = {}


def get_perpl_session(address: str) -> dict | None:
    cached = _perpl_sessions.get(address.lower())
    if cached:
        return cached
    # Try loading from DB (startup recovery)
    return None


def _encode_perpl_session(session_data: dict) -> str:
    """Serialize + encrypt a Perpl session dict for storage at rest."""
    import json as _json
    return encrypt_token(_json.dumps(session_data))


def _decode_perpl_session(stored: str) -> dict | None:
    """Decrypt a stored Perpl session. Falls back to legacy plaintext JSON so
    rows written before encryption was added still load."""
    import json as _json
    try:
        return _json.loads(decrypt_token(stored))
    except Exception:
        pass
    try:  # legacy plaintext row
        return _json.loads(stored)
    except Exception:
        return None


async def _load_perpl_session_from_db(address: str) -> dict | None:
    """Load persisted Perpl session from DB."""
    try:
        async_session = get_session_factory()
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.wallet_address == address.lower())
            )
            user = result.scalar_one_or_none()
            if user and user.perpl_auth_token_encrypted:
                data = _decode_perpl_session(user.perpl_auth_token_encrypted)
                if data is not None:
                    _perpl_sessions[address.lower()] = data
                return data
    except Exception:
        pass
    return None


async def get_perpl_session_async(address: str) -> dict | None:
    cached = _perpl_sessions.get(address.lower())
    if cached:
        return cached
    return await _load_perpl_session_from_db(address)


@router.post("/perpl-connect")
async def perpl_connect(
    req: PerplConnectRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Proxy: submit signature to Perpl, store session with cookies.

    Auth-gated: the caller must present a valid app JWT, and may only store a
    Perpl session for their OWN wallet. Cookies are encrypted at rest."""
    if req.address.lower() != user.wallet_address.lower():
        raise HTTPException(status_code=403, detail="Address does not match authenticated wallet")

    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{settings.PERPL_REST_URL}/api/v1/auth/connect",
            json=req.model_dump(),
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Perpl connect error: {resp.text}")

        data = resp.json()
        session_data = {
            "nonce": data.get("nonce", ""),
            "cookies": dict(resp.cookies),
        }

        # Cache in memory
        _perpl_sessions[req.address.lower()] = session_data

        # Persist to DB (ENCRYPTED) so it survives backend restarts
        try:
            async_session = get_session_factory()
            async with async_session() as session:
                result = await session.execute(
                    select(User).where(User.wallet_address == req.address.lower())
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    db_user.perpl_auth_token_encrypted = _encode_perpl_session(session_data)
                    await session.commit()
        except Exception:
            logger.exception("Failed to persist Perpl session")

        return data


# ---------------------------------------------------------------------------
# Perpl API-key enrollment (one-click trading)
#
# Enrollment endpoints are proxied server-side WITHOUT an Origin header:
# Perpl rejects browser Origins that are not whitelisted, but origin-less
# server-to-server calls are accepted (verified empirically 2026-07-27).
# The Ed25519 private key is generated and kept in the user's browser and
# never reaches this server — we only relay the public key / signatures.
# ---------------------------------------------------------------------------


class ApiKeyPayloadReq(BaseModel):
    public_key: str
    scope_mask: int = 3
    label: str = "SMINDEX"
    expires_at: int | None = None


class ApiKeyEnrollReq(BaseModel):
    typed_data: dict
    mac: str
    signature: str
    pop_signature: str


@router.post("/perpl-apikey-payload")
async def perpl_apikey_payload(
    req: ApiKeyPayloadReq,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Proxy: request an API-key enrollment payload (EIP-712) from Perpl.

    The address is ALWAYS the JWT wallet — a client cannot enroll a key
    against someone else's profile through us."""
    import httpx
    if req.scope_mask not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="scope_mask must be 1, 2 or 3")
    body = {
        "chain_id": settings.PERPL_CHAIN_ID,
        "address": user.wallet_address,
        "public_key": req.public_key,
        "scope_mask": req.scope_mask,
        "label": req.label[:64] or "SMINDEX",
    }
    if req.expires_at:
        body["expires_at"] = req.expires_at
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{settings.PERPL_REST_URL}/api/v1/api-key/payload", json=body)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Perpl api-key payload error: {resp.text}")
        return resp.json()


@router.post("/perpl-apikey-enroll")
async def perpl_apikey_enroll(
    req: ApiKeyEnrollReq,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Proxy: complete API-key enrollment on Perpl. Returns ApiKeyInfo
    (contains the opaque api_key token — stored client-side only)."""
    import httpx
    body = {
        "chain_id": settings.PERPL_CHAIN_ID,
        "address": user.wallet_address,
        "typed_data": req.typed_data,
        "mac": req.mac,
        "signature": req.signature,
        "pop_signature": req.pop_signature,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{settings.PERPL_REST_URL}/api/v1/api-key/enroll", json=body)
        if resp.status_code == 409:
            raise HTTPException(status_code=409, detail="This key is already registered on Perpl.")
        if resp.status_code == 423:
            raise HTTPException(status_code=423, detail="Perpl API-key limit reached (16). Revoke an old key at app.perpl.xyz/apikeys and retry.")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Perpl api-key enroll error: {resp.text}")
        return resp.json()


class ApiKeySignedProxyReq(BaseModel):
    """A request the BROWSER already signed with its Ed25519 key. We forward it
    verbatim — the signature covers method/target/timestamp/nonce/body, so we
    cannot alter it, only relay."""
    method: str
    target: str            # e.g. /v1/api-key/list (path Perpl expects in the canonical)
    body: str = ""
    api_key: str
    timestamp: str
    nonce: str
    signature: str


# Only key-management targets may be relayed here; trading/history stay on
# their own endpoints.
_APIKEY_PROXY_ALLOWED = ("/v1/api-key/list", "/v1/api-key/delete")


@router.post("/perpl-apikey-proxy")
async def perpl_apikey_signed_proxy(
    req: ApiKeySignedProxyReq,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Relay a browser-signed API-key management request (list / revoke).

    If Perpl rejects the signed attempt with 401 (key-management may require a
    web session instead), retry once with the user's stored Perpl session
    cookies as a fallback."""
    import httpx
    target_path = req.target.split("?")[0]
    if target_path not in _APIKEY_PROXY_ALLOWED:
        raise HTTPException(status_code=400, detail="Target not allowed")
    if req.method.upper() not in ("GET", "POST"):
        raise HTTPException(status_code=400, detail="Method not allowed")

    url = f"{settings.PERPL_REST_URL}/api{req.target}"
    signed_headers = {
        "X-API-Key": req.api_key,
        "X-API-Timestamp": req.timestamp,
        "X-API-Nonce": req.nonce,
        "X-API-Signature": req.signature,
    }
    if req.body:
        signed_headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.request(
            req.method.upper(), url, headers=signed_headers,
            content=req.body.encode() if req.body else None,
        )

        # Fallback: cookie-authenticated call (same auth the Perpl web UI uses).
        if resp.status_code == 401:
            perpl_session = await get_perpl_session_async(user.wallet_address)
            cookies = (perpl_session or {}).get("cookies", {})
            if cookies:
                resp = await client.request(
                    req.method.upper(), url,
                    headers={"Content-Type": "application/json"} if req.body else {},
                    cookies=cookies,
                    content=req.body.encode() if req.body else None,
                )

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Perpl error: {resp.text}")
        try:
            return {"ok": True, "data": resp.json()}
        except Exception:
            return {"ok": True, "data": resp.text}


@router.get("/server-time")
async def server_time() -> dict:
    """Millisecond epoch for client clock-skew correction (Perpl signatures
    require X-API-Timestamp within ±30s)."""
    import time as _t
    return {"now_ms": int(_t.time() * 1000)}


@router.post("/link-perpl")
async def link_perpl(req: LinkPerplRequest, user: User = Depends(get_authenticated_user)) -> dict:
    encrypted = encrypt_token(req.perpl_token)
    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one()
        db_user.perpl_auth_token_encrypted = encrypted
        await session.commit()

    return {"success": True, "message": "Perpl account linked successfully"}


class SetUsernameRequest(BaseModel):
    username: str


import re

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")


@router.post("/set-username")
async def set_username(req: SetUsernameRequest, user: User = Depends(get_authenticated_user)) -> dict:
    """Set a one-time username for the authenticated user."""
    username = req.username.strip()

    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-30 characters, letters/numbers/underscores only.",
        )

    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one()

        if db_user.username:
            raise HTTPException(status_code=400, detail="Username already set. It can only be set once.")

        # Check uniqueness
        existing = await session.execute(
            select(User).where(User.username == username)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken.")

        db_user.username = username
        try:
            await session.commit()
        except Exception:
            raise HTTPException(status_code=409, detail="Username already taken.")

    return {"success": True, "username": username}


@router.get("/usernames")
async def get_usernames(wallets: str = Query(..., description="Comma-separated wallet addresses")) -> dict:
    """Public endpoint: resolve wallet addresses to usernames.

    Returns {wallet_address: username_or_null} for each address.
    """
    addresses = [w.strip().lower() for w in wallets.split(",") if w.strip()]
    if not addresses or len(addresses) > 100:
        raise HTTPException(status_code=400, detail="Provide 1-100 comma-separated addresses.")

    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(User.wallet_address, User.username).where(
                User.wallet_address.in_(addresses)
            )
        )
        rows = result.all()

    mapping = {row.wallet_address: row.username for row in rows}
    # Fill in missing addresses as null
    for addr in addresses:
        if addr not in mapping:
            mapping[addr] = None

    return mapping
