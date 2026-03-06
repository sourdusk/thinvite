# Thinvite — link Twitch channel-point redemptions to single-use Discord invites.
# Copyright (C) 2026  sourk9
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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
