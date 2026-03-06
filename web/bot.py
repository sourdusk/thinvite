"""EventSub webhook subscription management.

Replaces the former twitchAPI-based WebSocket listener with a thin layer that
creates, deletes, and recovers Twitch EventSub webhook subscriptions.

Events are delivered to POST /eventsub/callback in main.py; no persistent
connections are maintained here.  Token refresh is handled entirely by the
expiry-loop scheduler (expiry.py).
"""
import logging
import os

import db
import twitch as twitch_api

logger = logging.getLogger()

# In-memory set of session IDs with a confirmed active EventSub subscription.
_subscriptions: set[str] = set()


async def subscribe(user_record: dict) -> bool:
    """Create an EventSub webhook subscription for this user.

    Requires the user record to have twitch_user_id AND discord_server_id set —
    we only subscribe when the streamer has completed both OAuth flows.
    Returns True if a subscription was successfully registered with Twitch.
    """
    sess_id = user_record.get("session_id")
    broadcaster_id = user_record.get("twitch_user_id")
    if not sess_id or not broadcaster_id or not user_record.get("discord_server_id"):
        return False

    app_token = await twitch_api.get_app_access_token()
    if not app_token:
        logger.error(f"Cannot subscribe for {sess_id}: failed to obtain app access token")
        return False

    callback_url = os.getenv("SITE_URL", "").rstrip("/") + "/eventsub/callback"
    secret = os.getenv("THINVITE_EVENTSUB_SECRET", "")

    sub_id = await twitch_api.create_eventsub_subscription(
        broadcaster_id, callback_url, secret, app_token
    )
    if not sub_id:
        logger.error(f"Failed to create EventSub subscription for {sess_id}")
        return False

    await db.set_eventsub_subscription(sess_id, sub_id)
    _subscriptions.add(sess_id)
    logger.info(
        f"EventSub subscription {sub_id} created for "
        f"{user_record.get('twitch_user_name', sess_id)}"
    )
    return True


async def unsubscribe(sess_id: str) -> None:
    """Delete the EventSub subscription for this session, if one exists."""
    _subscriptions.discard(sess_id)

    user = await db.get_user_by_session_id(sess_id)
    if user is None:
        return

    sub_id = user.get("eventsub_subscription_id")
    if not sub_id:
        return

    await db.clear_eventsub_subscription(sess_id)

    app_token = await twitch_api.get_app_access_token()
    if not app_token:
        logger.warning(
            f"Cannot delete subscription {sub_id} for {sess_id}: "
            "failed to obtain app access token"
        )
        return

    deleted = await twitch_api.delete_eventsub_subscription(sub_id, app_token)
    if deleted:
        logger.info(f"EventSub subscription {sub_id} deleted for {sess_id}")
    else:
        logger.warning(
            f"Could not delete EventSub subscription {sub_id} for {sess_id} "
            "(may have already been removed)"
        )


async def handle_revocation(sess_id: str) -> None:
    """Handle a Twitch-initiated revocation — update local state without calling DELETE.

    Called by the /eventsub/callback handler when Twitch sends a revocation
    notification (e.g. the broadcaster revoked the app's permissions).
    """
    _subscriptions.discard(sess_id)
    await db.clear_eventsub_subscription(sess_id)


def has_active_subscription(sess_id: str) -> bool:
    """Return True if a confirmed active EventSub subscription exists for sess_id."""
    return sess_id in _subscriptions


async def recover_subscriptions() -> None:
    """Verify and restore EventSub subscriptions at application startup.

    For every user with both Twitch and Discord connected:
    - If a subscription_id is stored and still 'enabled' on Twitch → add to
      the in-memory set (no action needed).
    - If a stored subscription is stale/revoked, or no subscription is stored →
      create a fresh one.
    """
    users = await db.get_all_bot_users()
    if not users:
        return

    app_token = await twitch_api.get_app_access_token()
    if not app_token:
        logger.error("Cannot recover subscriptions: failed to obtain app access token")
        return

    callback_url = os.getenv("SITE_URL", "").rstrip("/") + "/eventsub/callback"
    secret = os.getenv("THINVITE_EVENTSUB_SECRET", "")

    for user in users:
        sess_id = user["session_id"]
        stored_sub_id = user.get("eventsub_subscription_id")

        if stored_sub_id:
            status = await twitch_api.get_eventsub_subscription_status(
                stored_sub_id, app_token
            )
            if status == "enabled":
                _subscriptions.add(sess_id)
                logger.info(
                    f"Recovered active subscription {stored_sub_id} for "
                    f"{user.get('twitch_user_name', sess_id)}"
                )
                continue

            # Stale or unknown — clear and re-register below.
            logger.warning(
                f"Subscription {stored_sub_id} for {sess_id} has status {status!r}; "
                "re-registering"
            )
            await db.clear_eventsub_subscription(sess_id)

        # Create a fresh subscription.
        sub_id = await twitch_api.create_eventsub_subscription(
            user["twitch_user_id"], callback_url, secret, app_token
        )
        if sub_id:
            await db.set_eventsub_subscription(sess_id, sub_id)
            _subscriptions.add(sess_id)
            logger.info(
                f"Created subscription {sub_id} for "
                f"{user.get('twitch_user_name', sess_id)}"
            )
        else:
            logger.error(
                f"Failed to create EventSub subscription for {sess_id} during recovery"
            )
