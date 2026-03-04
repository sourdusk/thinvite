import logging
import os

import aiohttp
import aiomysql
from dotenv import load_dotenv

import db
import sanitize

logger = logging.getLogger()

load_dotenv()


async def update_info(sess_id: str, code: str, guild_id: str) -> (bool, str):
    if not sanitize.is_valid_snowflake(guild_id):
        return (False, "Invalid guild ID")
    token = await get_discord_token(code)
    if token is None:
        return (False, "Failed to get auth token")
    user_id = await get_user_info(token)
    if user_id is None:
        return (False, "Failed to get user info")
    async with db._pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # Check to see if there's an existing discord server
            await cur.execute(
                "SELECT id FROM users WHERE discord_server_id = %s", (guild_id,)
            )
            res = await cur.fetchone()
            if res is not None and res["id"] is not None:
                await cur.execute(
                    "UPDATE users SET session_id = %s WHERE discord_server_id = %s",
                    (sess_id, guild_id),
                )
            await cur.execute(
                """
                UPDATE users SET
                session_id = %s,
                discord_user_id = %s,
                discord_server_id = %s,
                discord_auth_code = %s
                WHERE session_id = %s
                """,
                (sess_id, user_id, guild_id, code, sess_id),
            )
            if cur.rowcount != 1:
                return (False, "Failed to update user info")
            return (True, "Success")


async def get_discord_token(code: str) -> str:
    async with aiohttp.ClientSession(
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    ) as session:
        async with session.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": os.getenv("THINVITE_DISCORD_ID"),
                "client_secret": os.getenv("THINVITE_DISCORD_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://thinvite.sourk9.com/api/discord",
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
        headers={"Authorization": f"Bot {bot_token}"}
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
        headers={
            "Authorization": f"Bearer {token}",
        }
    ) as session:
        async with session.get("https://discord.com/api/v10/users/@me") as resp:
            res = await resp.json()
            if res is not None and "id" in res:
                return res["id"]
            return None
