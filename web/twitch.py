import os
import logging
import urllib.parse
import time

import aiohttp
from dotenv import load_dotenv

import db
import sanitize

logger = logging.getLogger()

load_dotenv()

_TIMEOUT = aiohttp.ClientTimeout(total=10)


def generate_auth_code_link(state: str, force_verify: bool = False) -> str:
    params = [
        f"client_id={os.getenv('THINVITE_TWITCH_ID')}",
        f"force_verify={'true' if force_verify else 'false'}",
        f"redirect_uri={urllib.parse.quote('https://thinvite.sourk9.com/api/twitch/auth_code')}",
        "response_type=code",
        "scope=channel:read:redemptions channel:bot chat:read chat:edit user:write:chat",
        f"state={state}",
    ]
    param_string = "&".join(params)
    return f"https://id.twitch.tv/oauth2/authorize?{param_string}"


async def get_auth_token(code: str) -> dict:
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": os.getenv("THINVITE_TWITCH_ID"),
                "client_secret": os.getenv("THINVITE_TWITCH_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://thinvite.sourk9.com/api/twitch/auth_code",
            },
        ) as resp:
            res = await resp.json()
            if "access_token" not in res:
                return None
            return res


async def get_user_from_db(sess_id: str) -> dict:
    return await db.get_user_by_session_id(sess_id)


async def get_user_info(token: str) -> dict:
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
        }
    ) as session:
        async with session.get("https://api.twitch.tv/helix/users") as resp:
            res = await resp.json()
            if res is not None and res.get("data"):
                return res["data"][0]
            return None


async def get_channel_redeems(sess_id: str) -> dict:
    user = await get_user_from_db(sess_id)
    if user is None:
        return None
    id = user["twitch_user_id"]
    token = user["twitch_auth_token"]
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
        }
    ) as session:
        async with session.get(
            f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={id}"
        ) as resp:
            res = await resp.json()
            if res is not None and "data" in res:
                return {x["id"]: x["title"] for x in res["data"]}
            return None


async def update_twitch_info(
    sess_id: str, token_info: dict, user_info: dict, code: str
) -> bool:
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE users SET
                session_id = %s,
                twitch_user_id = %s,
                twitch_user_name = %s,
                twitch_auth_code = %s,
                twitch_auth_token = %s,
                twitch_token_expiry = %s,
                twitch_token_refresh_code = %s
                WHERE session_id = %s
                """,
                (
                    sess_id,
                    user_info["id"],
                    user_info["login"],
                    code,
                    token_info["access_token"],
                    token_info["expires_in"] + int(time.time()),
                    token_info["refresh_token"],
                    sess_id,
                ),
            )
            if cur.rowcount != 1:
                return False
            return True


async def init_login(sess_id: str, code: str) -> (bool, str):
    token_info = await get_auth_token(code)
    if token_info is None:
        return (False, "Failed to get auth token")
    user_info = await get_user_info(token_info["access_token"])
    if user_info is None:
        return (False, "Failed to get user info")
    return (
        await update_twitch_info(sess_id, token_info, user_info, code),
        "Login successful",
    )


async def user_exists(sess_id: str) -> bool:
    res = await db.get_user_by_session_id(sess_id)
    return res is not None and res.get("twitch_user_id") is not None


async def update_twitch_redeem(sess_id: str, redeem_id: str) -> bool:
    if not sanitize.is_valid_uuid(redeem_id):
        return False

    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET twitch_redeem_id = %s WHERE session_id = %s",
                (redeem_id, sess_id),
            )
            if cur.rowcount != 1:
                return False
            return True


def generate_viewer_auth_link(state: str) -> str:
    params = [
        f"client_id={os.getenv('THINVITE_TWITCH_ID')}",
        "force_verify=true",
        f"redirect_uri={urllib.parse.quote('https://thinvite.sourk9.com/api/twitch/viewer_auth')}",
        "response_type=code",
        "scope=user:read:email",
        f"state={state}",
    ]
    param_string = "&".join(params)
    return f"https://id.twitch.tv/oauth2/authorize?{param_string}"


async def get_viewer_token(code: str) -> dict:
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": os.getenv("THINVITE_TWITCH_ID"),
                "client_secret": os.getenv("THINVITE_TWITCH_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://thinvite.sourk9.com/api/twitch/viewer_auth",
            },
        ) as resp:
            res = await resp.json()
            if "access_token" not in res:
                return None
            return res


async def revoke_token(token: str) -> None:
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        await session.post(
            "https://id.twitch.tv/oauth2/revoke",
            data={
                "client_id": os.getenv("THINVITE_TWITCH_ID"),
                "token": token,
            },
        )


async def lookup_user_by_name(sess_id: str, username: str) -> dict:
    if not sanitize.is_valid_twitch_username(username):
        return None
    user = await get_user_from_db(sess_id)
    if user is None:
        return None
    token = user["twitch_auth_token"]
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
        }
    ) as session:
        async with session.get(
            f"https://api.twitch.tv/helix/users?login={urllib.parse.quote(username)}"
        ) as resp:
            res = await resp.json()
            if res is not None and res.get("data"):
                return res["data"][0]
            return None


async def get_set_redeem(sess_id: str) -> str:
    async with db._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT twitch_redeem_id FROM users WHERE session_id = %s", (sess_id,)
            )
            res = await cur.fetchone()
            if res is not None and res[0] is not None:
                return res[0]
            return None
