"""HMAC signing + verification for stage_order deep links.

Why: a `stage` payload is consumed by the terminal frontend and pre-fills an
order form. Without binding to a specific user + short expiry, an attacker
who can call `stage_order` (or who steals a deep link) can phish any logged-in
user into clicking Place on a foreign-crafted order. We sign the payload with
an HMAC keyed by `JWT_SECRET` (domain-separated) and bind it to `uid` + `exp`.
The frontend POSTs the encoded blob to `/api/mcp-stage/verify`, which checks:

  1. base64url + json decodes
  2. HMAC matches (constant-time compare)
  3. exp > now
  4. uid == currently authenticated terminal user

Only after all four does the frontend dispatch the staged order into the form.
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

from app.config import settings


_DOMAIN = b"perpl-mcp-stage-v1"


def _key() -> bytes:
    return hashlib.sha256(_DOMAIN + settings.JWT_SECRET.encode("utf-8")).digest()


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_payload(payload: dict, user_id: int, ttl_seconds: Optional[int] = None) -> str:
    """Return a base64url(json) blob with embedded uid+exp+sig."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.MCP_STAGE_TTL_SECONDS
    body = {
        "p": payload,
        "uid": int(user_id),
        "exp": int(time.time()) + max(30, int(ttl)),
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_key(), raw, hashlib.sha256).digest()
    envelope = _b64u_encode(raw) + "." + _b64u_encode(sig)
    return envelope


def verify_payload(envelope: str, expected_user_id: int) -> dict[str, Any]:
    """Verify HMAC, expiry, and uid binding. Returns the inner payload dict.
    Raises ValueError on any failure (caller should map to 400/401).
    """
    if not envelope or "." not in envelope:
        raise ValueError("Malformed stage envelope")
    raw_b64, sig_b64 = envelope.split(".", 1)
    try:
        raw = _b64u_decode(raw_b64)
        sig = _b64u_decode(sig_b64)
    except Exception:
        raise ValueError("Stage envelope is not valid base64url")

    expected_sig = hmac.new(_key(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, sig):
        raise ValueError("Stage signature mismatch")

    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("Stage payload is not valid JSON")

    exp = int(body.get("exp", 0))
    if exp < int(time.time()):
        raise ValueError("Stage payload has expired")

    uid = int(body.get("uid", 0))
    if uid != int(expected_user_id):
        raise ValueError("Stage payload was issued for a different user")

    payload = body.get("p")
    if not isinstance(payload, dict):
        raise ValueError("Stage payload missing inner body")
    return payload
