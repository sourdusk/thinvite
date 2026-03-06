"""Background task: expire pending redemptions after 24 hours and refund points.

After a redemption is created (viewer redeems channel points), the viewer has
24 hours to visit /redeem and claim their Discord invite.  If they don't, this
module automatically:
  1. Calls the Twitch API to CANCEL the redemption (refunds channel points).
  2. Marks the DB row as revoked so it no longer appears as pending.

The loop runs every _CHECK_INTERVAL_SECONDS (default 5 minutes).
"""
import asyncio
import logging

import db
import twitch as twitch_api

logger = logging.getLogger()

_CHECK_INTERVAL_SECONDS = 5 * 60  # check every 5 minutes


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
                    f"Twitch cancel call returned False for redemption {redemption_id}; "
                    "marking expired in DB anyway"
                )
        except Exception:
            logger.exception(
                f"Error cancelling redemption {redemption_id} on Twitch; "
                "marking expired in DB anyway"
            )

        # Always mark expired in the DB so we don't retry on the next cycle.
        try:
            await db.expire_redemption(redemption_id)
        except Exception:
            logger.exception(
                f"Failed to mark redemption {redemption_id} as expired in DB"
            )


async def start_expiry_loop() -> None:
    """Run expire_old_redemptions periodically for the lifetime of the process.

    Sleeps *before* the first check so the application has time to finish
    starting up (pool, bot listeners, etc.) before the first DB query.
    """
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        await expire_old_redemptions()
