"""Twitch Extension PubSub — send whisper messages to viewer panels."""

import json
import logging
import os

import aiohttp

from ext_auth import sign_ebs_jwt

log = logging.getLogger(__name__)

_PUBSUB_URL = "https://api.twitch.tv/helix/extensions/pubsub"


async def send_whisper(channel_id: str, viewer_user_id: str, message: dict) -> None:
    """Send a PubSub whisper to a specific viewer's extension panel.

    Fire-and-forget: logs errors but never raises.
    """
    try:
        client_id = os.environ["TWITCH_EXT_CLIENT_ID"]
        token = sign_ebs_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "target": [f"whisper-{viewer_user_id}"],
            "broadcaster_id": channel_id,
            "message": json.dumps(message),
        })
        async with aiohttp.ClientSession() as session:
            async with session.post(_PUBSUB_URL, headers=headers, data=body) as resp:
                if resp.status != 204:
                    text = await resp.text()
                    log.warning("PubSub whisper failed (%s): %s", resp.status, text)
    except Exception:
        log.exception("PubSub whisper error")
