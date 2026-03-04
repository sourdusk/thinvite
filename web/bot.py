import asyncio
import logging
import os

import aiohttp
from twitchAPI.twitch import Twitch
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.type import AuthScope, MissingScopeException

import db

logger = logging.getLogger()

_listeners: dict = {}  # session_id -> (twitch_client, eventsub)
_needs_reauth: set = set()  # session_ids that failed due to missing scope

SCOPES = [
    AuthScope.CHANNEL_READ_REDEMPTIONS,
    AuthScope.CHAT_READ,
    AuthScope.CHAT_EDIT,
    AuthScope.USER_WRITE_CHAT,
]

SITE_URL = "https://thinvite.sourk9.com"


async def _send_chat_message(broadcaster_id: str, token: str, message: str) -> None:
    async with aiohttp.ClientSession(headers={
        "Authorization": f"Bearer {token}",
        "Client-Id": os.getenv("THINVITE_TWITCH_ID"),
        "Content-Type": "application/json",
    }) as session:
        resp = await session.post(
            "https://api.twitch.tv/helix/chat/messages",
            json={
                "broadcaster_id": broadcaster_id,
                "sender_id": broadcaster_id,
                "message": message,
            }
        )
        if resp.status != 200:
            logger.error(f"Failed to send chat message: {resp.status} {await resp.text()}")


async def _on_redemption(sess_id: str, event) -> None:
    user = await db.get_user_by_session_id(sess_id)
    if user is None:
        return
    redeem_id = user.get("twitch_redeem_id")
    if redeem_id and event.event.reward.id != redeem_id:
        return

    redeemer = event.event.user_name
    viewer_id = event.event.user_id

    # Store the pending redemption so the viewer can claim it at /redeem.
    await db.add_redemption(sess_id, viewer_id, redeemer)

    broadcaster_id = user["twitch_user_id"]
    token = user["twitch_auth_token"]
    message = f"@{redeemer} Head to {SITE_URL}/redeem to claim your Discord invite!"
    await _send_chat_message(broadcaster_id, token, message)


async def start_listener(user_record: dict) -> None:
    sess_id = user_record["session_id"]
    await stop_listener(sess_id)

    try:
        twitch_client = await Twitch(
            os.getenv("THINVITE_TWITCH_ID"),
            os.getenv("THINVITE_TWITCH_SECRET"),
        )
        await twitch_client.set_user_authentication(
            user_record["twitch_auth_token"],
            SCOPES,
            user_record["twitch_token_refresh_code"],
        )

        eventsub = EventSubWebsocket(twitch_client)
        # start() is synchronous and blocks until the WebSocket handshake completes,
        # so run it in a thread to avoid blocking the async event loop.
        await asyncio.to_thread(eventsub.start)

        async def callback(event):
            await _on_redemption(sess_id, event)

        await eventsub.listen_channel_points_custom_reward_redemption_add(
            user_record["twitch_user_id"], callback
        )

        _listeners[sess_id] = (twitch_client, eventsub)
        _needs_reauth.discard(sess_id)
        logger.info(f"Bot listener started for {user_record.get('twitch_user_name', sess_id)}")
    except MissingScopeException:
        logger.warning(f"Missing scope for {sess_id}, user must re-authenticate")
        _needs_reauth.add(sess_id)
    except Exception:
        logger.exception(f"Failed to start bot listener for {sess_id}")


async def stop_listener(sess_id: str) -> None:
    if sess_id not in _listeners:
        return
    twitch_client, eventsub = _listeners.pop(sess_id)
    try:
        await eventsub.stop()
        await twitch_client.close()
    except Exception:
        logger.exception(f"Error stopping listener for {sess_id}")


def needs_reauth(sess_id: str) -> bool:
    return sess_id in _needs_reauth


async def start_all_listeners() -> None:
    users = await db.get_all_bot_users()
    for user in users:
        await start_listener(user)
