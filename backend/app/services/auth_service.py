import base64
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from jose import jwt, JWTError

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

JWT_ALGORITHM = "HS256"
_FERNET_SALT = b"perpl-copy-trade-salt-v1"


def _get_fernet() -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(settings.JWT_SECRET.encode("utf-8")))
    return Fernet(key)


DEFAULT_SIWE_HOST = "smindex.xyz"


def generate_siwe_payload(address: str, chain_id: int = 143,
                          host: str | None = None) -> dict:
    """SIWE payload. `domain`/`uri` follow the host the user is actually on.

    These are NOT links — they are what the wallet shows the user when it asks
    them to sign ("<domain> wants you to sign in"). Hardcoding one host meant a
    visitor on one host was asked to sign for another, which is both confusing
    and exactly the shape of a phishing prompt. Deriving it from
    the request makes each host sign for itself.

    Safe to change: the signature check in routers/auth.py
    (`SiweMessage.verify`) validates the signature over the message the client
    sent and does not pin the domain; replay protection is the server-issued
    nonce, which is unaffected."""
    nonce = secrets.token_hex(16)
    issued_at = datetime.now(timezone.utc).isoformat()
    h = (host or DEFAULT_SIWE_HOST).split(":")[0].strip().lower() or DEFAULT_SIWE_HOST
    return {
        "domain": h,
        "address": address,
        "statement": "Sign in to SMINDEX",
        "uri": f"https://{h}",
        "version": "1",
        "chain_id": chain_id,
        "nonce": nonce,
        "issued_at": issued_at,
    }


def verify_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as exc:
        raise ValueError(f"Invalid JWT: {exc}") from exc


def encrypt_token(token: str) -> str:
    f = _get_fernet()
    return f.encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted: str) -> str:
    f = _get_fernet()
    return f.decrypt(encrypted.encode("utf-8")).decode("utf-8")
