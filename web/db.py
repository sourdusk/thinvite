import os
import logging

import aiomysql
from dotenv import load_dotenv
import pymysql

load_dotenv()

db_info = {
    "user": "thinvite",
    "password": os.getenv("MARIADB_PASSWORD"),
    "database": "thinvite",
    "host": "thinvite-db",
}

logger = logging.getLogger()


async def get_user_by_session_id(sess_id: str) -> dict:
    async with aiomysql.connect(**db_info) as conn:
        async with conn.cursor(cursor_factory=pymysql.extras.DictCursor) as cur:
            await cur.execute("SELECT * FROM users WHERE session_id = %s", (sess_id,))
            return await cur.fetchone()


async def ensure_db_user(sess_id: str) -> bool:
    if await get_user_by_session_id(sess_id) is not None:
        return True
    async with aiomysql.connect(**db_info) as conn:
        async with conn.cursor(cursor_factory=pymysql.extras.DictCursor) as cur:
            cur.autocommit = True
            res = await cur.execute(
                "INSERT INTO users (session_id) VALUES (%s)", (sess_id,)
            )
            if res is None or res.rowcount != 1:
                return False
            return True


async def update_twitch_auth_code(sess_id: str, code: str) -> bool:
    await ensure_db_user(sess_id)
    async with aiomysql.connect(**db_info) as conn:
        async with conn.cursor(cursor_factory=pymysql.extras.DictCursor) as cur:
            cur.autocommit = True
            await cur.execute(
                "UPDATE users SET twitch_auth_code = %s WHERE session_id = %s",
                (
                    code,
                    sess_id,
                ),
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_auth_token(
    sess_id: str, token: str, expiry: int, refresh_token: str
) -> bool:
    await ensure_db_user(sess_id)
    async with aiomysql.connect(**db_info) as conn:
        async with conn.cursor(cursor_factory=pymysql.extras.DictCursor) as cur:
            cur.autocommit = True
            await cur.execute(
                "UPDATE users SET twitch_auth_token = %s, twitch_token_expiry = %s, twitch_token_refresh_code = %s WHERE session_id = %s",
                (
                    token,
                    expiry,
                    refresh_token,
                    sess_id,
                ),
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_user_info(sess_id: str, username: str, user_id: str) -> bool:
    await ensure_db_user(sess_id)
    async with aiomysql.connect(**db_info) as conn:
        async with conn.cursor(cursor_factory=pymysql.extras.DictCursor) as cur:
            cur.autocommit = True
            await cur.execute(
                "UPDATE users SET twitch_user_id = %s, twitch_user_name = %s WHERE session_id = %s",
                (user_id, username, sess_id),
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_redeem(sess_id: str, redeem_id: str) -> None:
    async with aiomysql.connect(**db_info) as conn:
        async with conn.cursor(cursor_factory=pymysql.extras.DictCursor) as cur:
            cur.autocommit = True
            await cur.execute(
                "UPDATE users SET twitch_redeem_id = %s WHERE session_id = %s",
                (redeem_id, sess_id),
            )


async def delete_twitch_user(sess_id: str) -> bool:
    pass
