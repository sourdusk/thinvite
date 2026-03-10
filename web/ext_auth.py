"""Twitch Extension JWT verification and EBS JWT signing."""

import base64
import logging
import os
import time

import jwt

log = logging.getLogger(__name__)

_secret_bytes: bytes | None = None


def _get_secret() -> bytes:
    global _secret_bytes
    if _secret_bytes is None:
        b64 = os.environ["TWITCH_EXT_SECRET"]
        _secret_bytes = base64.b64decode(b64)
    return _secret_bytes


def verify_ext_jwt(token: str) -> dict | None:
    """Verify a Twitch Extension JWT. Returns claims dict or None."""
    try:
        claims = jwt.decode(token, _get_secret(), algorithms=["HS256"])
    except jwt.exceptions.PyJWTError:
        return None

    if "user_id" not in claims:
        return None

    return claims


def sign_ebs_jwt() -> str:
    """Sign a JWT for EBS-to-Twitch API calls (PubSub, etc.)."""
    owner_id = os.environ["TWITCH_EXT_OWNER_ID"]
    payload = {
        "user_id": owner_id,
        "role": "external",
        "exp": int(time.time()) + 120,
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")
