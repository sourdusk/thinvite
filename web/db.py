import os
import logging
from contextlib import asynccontextmanager

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
        **db_info, minsize=2, maxsize=25, autocommit=True,
        pool_recycle=3600,  # recycle connections older than 1 hour
    )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


@asynccontextmanager
async def _acquire():
    """Acquire a DB connection, reinitialising the pool once on failure."""
    global _pool
    try:
        async with _pool.acquire() as conn:
            yield conn
    except (aiomysql.OperationalError, AttributeError, Exception) as exc:
        logger.warning(f"DB connection error ({exc}), reinitialising pool…")
        try:
            if _pool is not None:
                _pool.close()
                await _pool.wait_closed()
        except Exception:
            pass
        _pool = None
        await init_pool()
        async with _pool.acquire() as conn:
            yield conn


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

async def get_user_by_session_id(sess_id: str) -> dict:
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM users WHERE session_id = %s", (sess_id,))
            return await cur.fetchone()


async def ensure_db_user(sess_id: str) -> bool:
    if await get_user_by_session_id(sess_id) is not None:
        return True
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "INSERT INTO users (session_id) VALUES (%s)", (sess_id,)
            )
            if cur.rowcount != 1:
                return False
            return True


async def update_twitch_auth_code(sess_id: str, code: str) -> bool:
    await ensure_db_user(sess_id)
    async with _acquire() as conn:
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
    async with _acquire() as conn:
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
    async with _acquire() as conn:
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
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE users SET twitch_redeem_id = %s WHERE session_id = %s",
                (redeem_id, sess_id),
            )


async def disconnect_twitch(sess_id: str) -> None:
    """Clear all Twitch-related fields for the given session."""
    async with _acquire() as conn:
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
    async with _acquire() as conn:
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
    async with _acquire() as conn:
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
    streamer_sess_id: str,
    viewer_user_id: str,
    viewer_user_name: str,
    twitch_redemption_id: str = None,
    twitch_reward_id: str = None,
) -> None:
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "INSERT INTO redemptions "
                "(streamer_session_id, viewer_twitch_user_id, viewer_twitch_user_name, "
                "twitch_redemption_id, twitch_reward_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (streamer_sess_id, viewer_user_id, viewer_user_name,
                 twitch_redemption_id, twitch_reward_id),
            )


async def has_pending_redemption(streamer_sess_id: str, viewer_user_id: str) -> bool:
    """Return True if the viewer already has an unfulfilled, unrevoked redemption."""
    async with _acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM redemptions "
                "WHERE streamer_session_id = %s AND viewer_twitch_user_id = %s "
                "AND fulfilled_at IS NULL AND revoked_at IS NULL",
                (streamer_sess_id, viewer_user_id),
            )
            return await cur.fetchone() is not None


async def get_pending_redemptions_for_viewer(viewer_user_id: str) -> list:
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT r.id, r.streamer_session_id, u.discord_server_id,
                       u.twitch_user_name AS streamer_name,
                       u.twitch_user_id AS streamer_twitch_id,
                       u.twitch_auth_token AS streamer_token,
                       r.twitch_redemption_id, r.twitch_reward_id
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
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE redemptions SET fulfilled_at = NOW(), invite_url = %s "
                "WHERE id = %s",
                (invite_url, redemption_id),
            )


async def revoke_redemption(redemption_id: int, streamer_sess_id: str) -> bool:
    """Revoke a pending redemption owned by *streamer_sess_id*.

    Returns True if the row was found and revoked, False if it was not found,
    already revoked, already fulfilled, or owned by a different streamer.
    """
    async with _acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE redemptions SET revoked_at = NOW() "
                "WHERE id = %s AND streamer_session_id = %s "
                "AND revoked_at IS NULL AND fulfilled_at IS NULL",
                (redemption_id, streamer_sess_id),
            )
            return cur.rowcount == 1


async def revoke_all_pending_redemptions(streamer_sess_id: str) -> list:
    """Revoke all pending (unfulfilled, unrevoked) redemptions for *streamer_sess_id*.

    Returns a list of dicts with 'id' and 'invite_url' for each revoked row
    so the caller can attempt to invalidate Discord invites.
    """
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, invite_url FROM redemptions "
                "WHERE streamer_session_id = %s "
                "AND fulfilled_at IS NULL AND revoked_at IS NULL",
                (streamer_sess_id,),
            )
            pending = list(await cur.fetchall())
            if pending:
                ids = [r["id"] for r in pending]
                placeholders = ",".join(["%s"] * len(ids))
                await cur.execute(
                    f"UPDATE redemptions SET revoked_at = NOW() "
                    f"WHERE id IN ({placeholders})",
                    ids,
                )
            return pending


async def add_manual_redemption(
    streamer_sess_id: str, viewer_user_id: str, viewer_user_name: str
) -> None:
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "INSERT INTO redemptions "
                "(streamer_session_id, viewer_twitch_user_id, viewer_twitch_user_name, "
                "is_manual) VALUES (%s, %s, %s, TRUE)",
                (streamer_sess_id, viewer_user_id, viewer_user_name),
            )


async def get_redemptions_for_streamer(
    streamer_sess_id: str, limit: int = 200
) -> list:
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM redemptions WHERE streamer_session_id = %s "
                "ORDER BY redeemed_at DESC LIMIT %s",
                (streamer_sess_id, limit),
            )
            return list(await cur.fetchall())


async def get_all_bot_users() -> list:
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM users "
                "WHERE twitch_user_id IS NOT NULL AND discord_server_id IS NOT NULL"
            )
            return list(await cur.fetchall())


async def get_user_by_twitch_id(twitch_user_id: str) -> dict:
    """Return the user row matching the given Twitch broadcaster ID, or None."""
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM users WHERE twitch_user_id = %s", (twitch_user_id,)
            )
            return await cur.fetchone()


async def set_eventsub_subscription(sess_id: str, sub_id: str) -> None:
    """Store the Twitch EventSub subscription ID for a session."""
    async with _acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET eventsub_subscription_id = %s WHERE session_id = %s",
                (sub_id, sess_id),
            )


async def clear_eventsub_subscription(sess_id: str) -> None:
    """Clear the stored EventSub subscription ID for a session."""
    async with _acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET eventsub_subscription_id = NULL WHERE session_id = %s",
                (sess_id,),
            )


async def rotate_session(old_id: str, new_id: str) -> None:
    """Rename a session row to a fresh token to mitigate session-fixation."""
    async with _acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET session_id = %s WHERE session_id = %s",
                (new_id, old_id),
            )


# ---------------------------------------------------------------------------
# Expiry helpers (24-hour auto-cancel)
# ---------------------------------------------------------------------------

async def get_users_with_expiring_tokens() -> list:
    """Return users whose Twitch access token expires within the next 30 minutes.

    Only includes users with both a stored token and refresh code (i.e. they
    have completed Twitch OAuth).  The 30-minute window gives the refresh task
    plenty of margin given it runs every 30 minutes.
    """
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT session_id, twitch_token_refresh_code "
                "FROM users "
                "WHERE twitch_auth_token IS NOT NULL "
                "AND twitch_token_refresh_code IS NOT NULL "
                "AND twitch_token_expiry IS NOT NULL "
                "AND twitch_token_expiry < UNIX_TIMESTAMP() + 1800"
            )
            return list(await cur.fetchall())


async def get_expired_pending_redemptions() -> list:
    """Return all non-manual pending redemptions that are more than 24 hours old.

    Only rows with a stored twitch_redemption_id can be refunded via the Twitch
    API; manual redemptions (added by the streamer dashboard) are excluded
    because they have no associated channel-point event to cancel.
    """
    async with _acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT r.id, r.twitch_redemption_id, r.twitch_reward_id,
                       u.twitch_user_id AS broadcaster_id,
                       u.twitch_auth_token AS token
                FROM redemptions r
                JOIN users u ON r.streamer_session_id = u.session_id
                WHERE r.fulfilled_at IS NULL
                  AND r.revoked_at IS NULL
                  AND r.is_manual = FALSE
                  AND r.twitch_redemption_id IS NOT NULL
                  AND r.redeemed_at < NOW() - INTERVAL 24 HOUR
                """
            )
            return list(await cur.fetchall())


async def expire_redemption(redemption_id: int) -> None:
    """Mark a single redemption as expired (sets revoked_at to now)."""
    async with _acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE redemptions SET revoked_at = NOW() WHERE id = %s",
                (redemption_id,),
            )
