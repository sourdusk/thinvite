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

import logging
import os

import aiohttp
import aiomysql
from dotenv import load_dotenv

import db
import sanitize

logger = logging.getLogger()

load_dotenv()

_SITE_URL = os.getenv("SITE_URL", "")
_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def get_user_guilds(token: str) -> list:
    """Return partial guild objects for all guilds the user belongs to."""
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT, headers={"Authorization": f"Bearer {token}"}
    ) as session:
        async with session.get("https://discord.com/api/v10/users/@me/guilds") as resp:
            if resp.status != 200:
                logger.error(f"Failed to fetch user guilds: {resp.status}")
                return None
            return await resp.json()


async def update_info(sess_id: str, code: str, guild_id: str) -> (bool, str):
    if not sanitize.is_valid_snowflake(guild_id):
        return (False, "Invalid guild ID")
    token = await get_discord_token(code)
    if token is None:
        return (False, "Failed to get auth token")

    # Verify the requesting user owns or manages the specified guild.
    guilds = await get_user_guilds(token)
    if guilds is None:
        return (False, "Failed to verify guild membership")
    _ADMINISTRATOR = 0x8
    _MANAGE_GUILD  = 0x20
    has_access = any(
        g["id"] == guild_id and (
            g.get("owner")
            or int(str(g.get("permissions", 0))) & (_ADMINISTRATOR | _MANAGE_GUILD)
        )
        for g in guilds
    )
    if not has_access:
        return (False, "You do not have permission to manage that Discord server")

    user_id = await get_user_info(token)
    if user_id is None:
        return (False, "Failed to get user info")
    async with db._acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # Only update the row that belongs to the current session.
            # Never look up or overwrite rows by guild_id — that would allow
            # a caller to pivot into another user's account by supplying a
            # guild_id that is already stored against a different session_id.
            await cur.execute(
                """
                UPDATE users SET
                discord_user_id = %s,
                discord_server_id = %s,
                discord_auth_code = %s
                WHERE session_id = %s
                """,
                (user_id, guild_id, code, sess_id),
            )
            if cur.rowcount != 1:
                return (False, "Failed to update user info")
            return (True, "Success")


async def get_discord_token(code: str) -> str:
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT, headers={"Content-Type": "application/x-www-form-urlencoded"}
    ) as session:
        async with session.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": os.getenv("THINVITE_DISCORD_ID"),
                "client_secret": os.getenv("THINVITE_DISCORD_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _SITE_URL + "/api/discord",
            },
        ) as resp:
            res = await resp.json()
            if "access_token" not in res:
                return None
            return res["access_token"]


async def create_invite(guild_id: str) -> str:
    """Create a single-use 24h Discord invite for the given guild using the bot token."""
    if not sanitize.is_valid_snowflake(guild_id):
        return None
    bot_token = os.getenv("THINVITE_DISCORD_BOT_TOKEN")
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT, headers={"Authorization": f"Bot {bot_token}"}
    ) as session:
        async with session.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels"
        ) as resp:
            if resp.status != 200:
                logger.error(f"Failed to get channels for guild {guild_id}: {resp.status}")
                return None
            channels = await resp.json()
        text_channel = next((c for c in channels if c.get("type") == 0), None)
        if text_channel is None:
            logger.error(f"No text channel found in guild {guild_id}")
            return None
        async with session.post(
            f"https://discord.com/api/v10/channels/{text_channel['id']}/invites",
            json={"max_age": 86400, "max_uses": 1, "unique": True},
        ) as resp:
            if resp.status != 200:
                logger.error(f"Failed to create invite: {resp.status} {await resp.text()}")
                return None
            res = await resp.json()
            return f"https://discord.gg/{res['code']}"


async def get_user_info(token: str) -> str:
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT, headers={"Authorization": f"Bearer {token}"}
    ) as session:
        async with session.get("https://discord.com/api/v10/users/@me") as resp:
            res = await resp.json()
            if res is not None and "id" in res:
                return res["id"]
            return None
