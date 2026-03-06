import os
import logging

import aiomysql
from dotenv import load_dotenv

load_dotenv()

db_info = {
    "user": "thinvite",
    "password": os.getenv("THINVITE_DB_PASSWORD"),
    "db": "thinvite",
    "host": "thinvite-db",
}

logger = logging.getLogger()

# ---------------------------------------------------------------------------
# Connection pool — initialised once at startup via init_pool().
# All connections in the pool use autocommit=True (safe for both reads and
# the single-statement writes this app performs).
# ---------------------------------------------------------------------------
_pool: aiomysql.Pool = None


async def init_pool() -> None:
    global _pool
    _pool = await aiomysql.create_pool(
        **db_info, minsize=2, maxsize=10, autocommit=True
    )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

async def get_user_by_session_id(sess_id: str) -> dict:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM users WHERE session_id = %s", (sess_id,))
            return await cur.fetchone()


async def ensure_db_user(sess_id: str) -> bool:
    if await get_user_by_session_id(sess_id) is not None:
        return True
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "INSERT INTO users (session_id) VALUES (%s)", (sess_id,)
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_auth_code(sess_id: str, code: str) -> bool:
    await ensure_db_user(sess_id)
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE users SET twitch_auth_code = %s WHERE session_id = %s",
                (code, sess_id),
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_auth_token(
    sess_id: str, token: str, expiry: int, refresh_token: str
) -> bool:
    await ensure_db_user(sess_id)
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE users SET twitch_auth_token = %s, twitch_token_expiry = %s, "
                "twitch_token_refresh_code = %s WHERE session_id = %s",
                (token, expiry, refresh_token, sess_id),
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_user_info(sess_id: str, username: str, user_id: str) -> bool:
    await ensure_db_user(sess_id)
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE users SET twitch_user_id = %s, twitch_user_name = %s "
                "WHERE session_id = %s",
                (user_id, username, sess_id),
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_redeem(sess_id: str, redeem_id: str) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE users SET twitch_redeem_id = %s WHERE session_id = %s",
                (redeem_id, sess_id),
            )


async def disconnect_twitch(sess_id: str) -> None:
    """Clear all Twitch-related fields for the given session."""
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                UPDATE users SET
                    twitch_user_id = NULL,
                    twitch_user_name = NULL,
                    twitch_auth_code = NULL,
                    twitch_auth_token = NULL,
                    twitch_token_expiry = NULL,
                    twitch_token_refresh_code = NULL,
                    twitch_redeem_id = NULL
                WHERE session_id = %s
                """,
                (sess_id,),
            )


async def disconnect_discord(sess_id: str) -> None:
    """Clear all Discord-related fields for the given session."""
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                UPDATE users SET
                    discord_user_id = NULL,
                    discord_server_id = NULL,
                    discord_auth_code = NULL
                WHERE session_id = %s
                """,
                (sess_id,),
            )


async def delete_user_and_all_records(sess_id: str) -> None:
    """Delete the user record and all associated redemptions."""
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "DELETE FROM redemptions WHERE streamer_session_id = %s",
                (sess_id,),
            )
            await cur.execute(
                "DELETE FROM users WHERE session_id = %s",
                (sess_id,),
            )


# ---------------------------------------------------------------------------
# Redemption helpers
# ---------------------------------------------------------------------------

async def add_redemption(
    streamer_sess_id: str, viewer_user_id: str, viewer_user_name: str
) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "INSERT INTO redemptions "
                "(streamer_session_id, viewer_twitch_user_id, viewer_twitch_user_name) "
                "VALUES (%s, %s, %s)",
                (streamer_sess_id, viewer_user_id, viewer_user_name),
            )


async def get_pending_redemptions_for_viewer(viewer_user_id: str) -> list:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT r.id, r.streamer_session_id, u.discord_server_id,
                       u.twitch_user_name AS streamer_name
                FROM redemptions r
                JOIN users u ON r.streamer_session_id = u.session_id
                WHERE r.viewer_twitch_user_id = %s
                  AND r.fulfilled_at IS NULL
                  AND r.revoked_at IS NULL
                """,
                (viewer_user_id,),
            )
            return list(await cur.fetchall())


async def fulfill_redemption(redemption_id: int, invite_url: str) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE redemptions SET fulfilled_at = NOW(), invite_url = %s "
                "WHERE id = %s",
                (invite_url, redemption_id),
            )


async def revoke_redemption(redemption_id: int, streamer_sess_id: str) -> None:
    """Revoke a redemption, but only when it belongs to *streamer_sess_id*."""
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE redemptions SET revoked_at = NOW() "
                "WHERE id = %s AND streamer_session_id = %s",
                (redemption_id, streamer_sess_id),
            )


async def add_manual_redemption(
    streamer_sess_id: str, viewer_user_id: str, viewer_user_name: str
) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "INSERT INTO redemptions "
                "(streamer_session_id, viewer_twitch_user_id, viewer_twitch_user_name, "
                "is_manual) VALUES (%s, %s, %s, TRUE)",
                (streamer_sess_id, viewer_user_id, viewer_user_name),
            )


async def get_redemptions_for_streamer(streamer_sess_id: str) -> list:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM redemptions WHERE streamer_session_id = %s "
                "ORDER BY redeemed_at DESC",
                (streamer_sess_id,),
            )
            return list(await cur.fetchall())


async def get_all_bot_users() -> list:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM users "
                "WHERE twitch_user_id IS NOT NULL AND discord_server_id IS NOT NULL"
            )
            return list(await cur.fetchall())


async def rotate_session(old_id: str, new_id: str) -> None:
    """Rename a session row to a fresh token to mitigate session-fixation."""
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET session_id = %s WHERE session_id = %s",
                (new_id, old_id),
            )
