import logging
import os

import aiohttp
from dotenv import load_dotenv
import aiopg
import psycopg2

import db

logger = logging.getLogger()

load_dotenv()


async def update_info(sess_id: str, code: str, guild_id: str) -> (bool, str):
    token = await get_discord_token(code)
    logger.error(token)
    if token is None:
        return (False, "Failed to get auth token")
    user_id = await get_user_info(token)
    if user_id is None:
        return (False, "Failed to get user info")
    async with aiopg.connect(**db.db_info) as conn:
        async with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.autocommit = True
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


async def get_user_info(token: str) -> str:
    async with aiohttp.ClientSession(
        headers={
            "Authorization": f"Bearer {token}",
        }
    ) as session:
        async with session.get("https://discord.com/api/v10/users/@me") as resp:
            res = await resp.json()
            logger.error(res)
            if res is not None and "id" in res:
                return res["id"]
            return None
