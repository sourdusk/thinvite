"""
Cloudflare Turnstile server-side verification.

Required environment variable (in web/.env):
    TURNSTILE_SECRET_KEY   Secret key from the Cloudflare Turnstile dashboard.
"""
import logging
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger()

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def verify_turnstile(token: str) -> bool:
    """Return True only when *token* is a genuine Turnstile response token.

    Returns False immediately for empty tokens or a missing secret key so the
    caller never needs to handle exceptions — any falsy result means "reject".
    """
    if not token:
        return False

    secret = os.getenv("TURNSTILE_SECRET_KEY", "")
    if not secret:
        logger.error("TURNSTILE_SECRET_KEY not configured — rejecting submission")
        return False

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            _VERIFY_URL,
            data={"secret": secret, "response": token},
        ) as resp:
            if resp.status != 200:
                logger.error("Turnstile siteverify returned HTTP %s", resp.status)
                return False
            data = await resp.json()
            if not data.get("success"):
                logger.warning(
                    "Turnstile verification failed: %s",
                    data.get("error-codes", []),
                )
                return False
            return True
