"""Pseudo-crontab: periodic background tasks run on a configurable schedule.

Tasks are defined in _SCHEDULE as (interval_seconds, name, coroutine_function).
The main loop ticks every 60 seconds and fires any task whose interval has
elapsed since it last ran.  Adding a new periodic job is a single line in
_SCHEDULE — no extra asyncio.create_task calls needed.

Current schedule
----------------
  expire_old_redemptions  — every  5 min  (24-hour pending redemption auto-cancel)
  refresh_expiring_tokens — every 30 min  (Twitch token proactive refresh)
"""
import asyncio
import logging
import time

import db
import twitch as twitch_api

logger = logging.getLogger()


# ---------------------------------------------------------------------------
# Task: expire pending redemptions older than 24 hours
# ---------------------------------------------------------------------------

async def expire_old_redemptions() -> None:
    """Find all redemptions older than 24 hours and cancel them on Twitch."""
    try:
        expired = await db.get_expired_pending_redemptions()
    except Exception:
        logger.exception("Failed to fetch expired redemptions from DB")
        return

    if not expired:
        return

    logger.info(f"Expiring {len(expired)} overdue redemption(s)")

    for row in expired:
        redemption_id = row["id"]
        try:
            cancelled = await twitch_api.cancel_redemption(
                row["broadcaster_id"],
                row["twitch_reward_id"],
                row["twitch_redemption_id"],
                row["token"],
            )
            if not cancelled:
                logger.warning(
                    f"Twitch cancel returned False for redemption {redemption_id}; "
                    "marking expired in DB anyway"
                )
        except Exception:
            logger.exception(
                f"Error cancelling redemption {redemption_id} on Twitch; "
                "marking expired in DB anyway"
            )

        # Always mark expired so we don't retry on the next cycle.
        try:
            await db.expire_redemption(redemption_id)
        except Exception:
            logger.exception(
                f"Failed to mark redemption {redemption_id} as expired in DB"
            )


# ---------------------------------------------------------------------------
# Task: proactively refresh Twitch tokens nearing expiry
# ---------------------------------------------------------------------------

async def refresh_expiring_tokens() -> None:
    """Refresh Twitch access tokens that expire within the next 30 minutes.

    With the webhook-based EventSub transport, no persistent bot listeners
    manage tokens automatically.  The scheduler is the sole refresh path for
    all users, so every expiring token is handled here.

    All eligible refreshes are issued concurrently via asyncio.gather().
    """
    try:
        users = await db.get_users_with_expiring_tokens()
    except Exception:
        logger.exception("Failed to fetch users with expiring Twitch tokens")
        return

    if not users:
        return

    logger.info(f"Refreshing Twitch tokens for {len(users)} user(s)")
    pending = users

    async def _refresh_one(user: dict) -> None:
        sess_id = user["session_id"]
        refresh_code = user["twitch_token_refresh_code"]
        try:
            result = await twitch_api.refresh_auth_token(refresh_code)
            if result is None:
                logger.warning(
                    f"Token refresh failed for session {sess_id}; "
                    "user may need to reconnect their Twitch account"
                )
                return
            await db.update_twitch_auth_token(
                sess_id,
                result["access_token"],
                result["expires_in"] + int(time.time()),
                result["refresh_token"],
            )
            logger.info(f"Token refreshed for session {sess_id}")
        except Exception:
            logger.exception(f"Error refreshing token for session {sess_id}")

    await asyncio.gather(*(_refresh_one(u) for u in pending))


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

_SCHEDULE = [
    {"name": "expire_old_redemptions",  "interval": 5 * 60,  "fn": expire_old_redemptions},
    {"name": "refresh_expiring_tokens", "interval": 30 * 60, "fn": refresh_expiring_tokens},
]


async def start_expiry_loop() -> None:
    """Tick every 60 seconds; run each task when its interval has elapsed.

    Sleeps before the first tick so the application finishes starting up
    (DB pool, EventSub subscriptions, etc.) before any task queries the DB.
    """
    last_run: dict[str, float] = {entry["name"]: 0.0 for entry in _SCHEDULE}

    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for entry in _SCHEDULE:
            if now - last_run[entry["name"]] >= entry["interval"]:
                last_run[entry["name"]] = now
                try:
                    await entry["fn"]()
                except Exception:
                    logger.exception(
                        f"Unhandled error in scheduled task '{entry['name']}'"
                    )
