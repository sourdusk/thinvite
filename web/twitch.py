# Thinvite — link Twitch channel-point redemptions to single-use Discord invites.
# Copyright (C) 2026  sourk9
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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

_SITE_URL = os.getenv("SITE_URL", "")

_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Scopes required for streamers.  The manage scope is needed to CANCEL or
# FULFILL redemptions via the Helix API; read-only is not sufficient.
# chat:read / chat:edit / channel:bot were only needed for the IRC-based bot
# and are no longer required now that we use the Helix chat messages endpoint.
_STREAMER_SCOPE = (
    "channel:read:redemptions channel:manage:redemptions "
    "user:write:chat moderator:read:followers"
)

# ---------------------------------------------------------------------------
# App access token — Client Credentials flow (cached in memory)
# ---------------------------------------------------------------------------
_app_token: str | None = None
_app_token_expiry: float = 0.0


async def get_app_access_token() -> str | None:
    """Return a valid app access token, refreshing it when near expiry."""
    global _app_token, _app_token_expiry
    if _app_token and time.time() < _app_token_expiry - 300:
        return _app_token
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": os.getenv("THINVITE_TWITCH_ID"),
                "client_secret": os.getenv("THINVITE_TWITCH_SECRET"),
                "grant_type": "client_credentials",
            },
        ) as resp:
            res = await resp.json()
            if "access_token" not in res:
                logger.error(f"Failed to get app access token: {res}")
                return None
            _app_token = res["access_token"]
            _app_token_expiry = time.time() + res.get("expires_in", 3600)
            return _app_token


# ---------------------------------------------------------------------------
# EventSub subscription management
# ---------------------------------------------------------------------------

async def create_eventsub_subscription(
    broadcaster_id: str,
    callback_url: str,
    secret: str,
    app_token: str,
) -> str | None:
    """Create a channel-point redemption EventSub subscription.

    Returns the subscription ID string on success, None on failure.
    Twitch responds with 202 Accepted; the subscription becomes 'enabled'
    once the challenge handshake with our callback URL completes.
    """
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json",
        },
    ) as session:
        async with session.post(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            json={
                "type": "channel.channel_points_custom_reward_redemption.add",
                "version": "1",
                "condition": {"broadcaster_user_id": broadcaster_id},
                "transport": {
                    "method": "webhook",
                    "callback": callback_url,
                    "secret": secret,
                },
            },
        ) as resp:
            if resp.status == 409:
                # Subscription already exists — look it up and return its ID.
                existing = await _find_existing_subscription(
                    broadcaster_id, app_token
                )
                if existing:
                    logger.info(
                        f"EventSub subscription already exists for {broadcaster_id}: "
                        f"{existing}"
                    )
                    return existing
                logger.error(
                    f"EventSub 409 for {broadcaster_id} but could not find "
                    "existing subscription"
                )
                return None
            if resp.status != 202:
                logger.error(
                    f"Failed to create EventSub subscription for {broadcaster_id}: "
                    f"{resp.status} {await resp.text()}"
                )
                return None
            res = await resp.json()
            return res["data"][0]["id"]


async def _find_existing_subscription(
    broadcaster_id: str, app_token: str
) -> str | None:
    """Find an existing channel-point redemption EventSub subscription for a broadcaster.

    Queries the Twitch EventSub subscriptions list filtered by type and returns
    the subscription ID if one matches the broadcaster, or None.
    """
    sub_type = "channel.channel_points_custom_reward_redemption.add"
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {app_token}",
        },
    ) as session:
        async with session.get(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            params={"type": sub_type},
        ) as resp:
            if resp.status != 200:
                return None
            res = await resp.json()
            for sub in res.get("data", []):
                condition = sub.get("condition", {})
                if condition.get("broadcaster_user_id") == broadcaster_id:
                    return sub["id"]
            return None


async def delete_eventsub_subscription(subscription_id: str, app_token: str) -> bool:
    """Delete an EventSub subscription by ID. Returns True on 204 No Content."""
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {app_token}",
        },
    ) as session:
        async with session.delete(
            f"https://api.twitch.tv/helix/eventsub/subscriptions?id={subscription_id}",
        ) as resp:
            if resp.status not in (204, 404):
                logger.error(
                    f"Unexpected status deleting subscription {subscription_id}: {resp.status}"
                )
            return resp.status == 204


async def get_eventsub_subscription_status(
    subscription_id: str, app_token: str
) -> str | None:
    """Return the status string of an EventSub subscription, or None if not found."""
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {app_token}",
        },
    ) as session:
        async with session.get(
            f"https://api.twitch.tv/helix/eventsub/subscriptions?id={subscription_id}",
        ) as resp:
            if resp.status != 200:
                return None
            res = await resp.json()
            data = res.get("data", [])
            return data[0].get("status") if data else None


# ---------------------------------------------------------------------------
# Helix chat messages
# ---------------------------------------------------------------------------

async def send_chat_message(
    broadcaster_id: str, sender_user_id: str, message: str, token: str
) -> bool:
    """Send a message to a channel via the Helix chat messages endpoint.

    Requires the sender token to have the user:write:chat scope.
    """
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    ) as session:
        async with session.post(
            "https://api.twitch.tv/helix/chat/messages",
            json={
                "broadcaster_id": broadcaster_id,
                "sender_id": sender_user_id,
                "message": message,
            },
        ) as resp:
            if resp.status != 200:
                logger.error(
                    f"Failed to send chat message to {broadcaster_id}: "
                    f"{resp.status} {await resp.text()}"
                )
                return False
            return True


def generate_auth_code_link(state: str, force_verify: bool = False) -> str:
    params = [
        f"client_id={os.getenv('THINVITE_TWITCH_ID')}",
        f"force_verify={'true' if force_verify else 'false'}",
        f"redirect_uri={urllib.parse.quote(_SITE_URL + '/api/twitch/auth_code')}",
        "response_type=code",
        f"scope={urllib.parse.quote(_STREAMER_SCOPE)}",
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
                "redirect_uri": _SITE_URL + "/api/twitch/auth_code",
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


async def get_user_by_id(user_id: str, token: str) -> dict | None:
    """Fetch a Twitch user by their numeric ID. Returns user dict or None."""
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "Client-Id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
        },
    ) as session:
        async with session.get(
            "https://api.twitch.tv/helix/users", params={"id": user_id},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            users = data.get("data", [])
            return users[0] if users else None


async def get_channel_redeems(sess_id: str) -> list | None:
    """Return the full list of custom reward objects, or None on error.

    On HTTP 401 the stored token is refreshed and the request retried once.
    """
    user = await get_user_from_db(sess_id)
    if user is None:
        return None
    broadcaster_id = user["twitch_user_id"]
    token = user["twitch_auth_token"]

    async def _call(tok: str):
        async with aiohttp.ClientSession(
            timeout=_TIMEOUT,
            headers={
                "client-id": os.getenv("THINVITE_TWITCH_ID"),
                "Authorization": f"Bearer {tok}",
            }
        ) as session:
            async with session.get(
                f"https://api.twitch.tv/helix/channel_points/custom_rewards"
                f"?broadcaster_id={broadcaster_id}"
            ) as resp:
                return resp.status, await resp.json()

    status, res = await _call(token)

    if status == 401:
        refresh_code = user.get("twitch_token_refresh_code")
        if refresh_code:
            logger.info(f"get_channel_redeems: 401 for session {sess_id}, refreshing token")
            new_tokens = await refresh_auth_token(refresh_code)
            if new_tokens:
                token = new_tokens["access_token"]
                await db.update_twitch_auth_token(
                    sess_id, token,
                    new_tokens["expires_in"] + int(time.time()),
                    new_tokens["refresh_token"],
                )
                status, res = await _call(token)

    if status == 200 and res is not None and "data" in res:
        return res["data"]

    logger.warning(f"get_channel_redeems failed: HTTP {status} — {res}")
    return None


async def update_twitch_info(
    sess_id: str, token_info: dict, user_info: dict, code: str
) -> bool:
    async with db._acquire() as conn:
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


async def update_twitch_redeem(sess_id: str, redeem_id: str | None) -> bool:
    if redeem_id is not None and not sanitize.is_valid_uuid(redeem_id):
        return False

    async with db._acquire() as conn:
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
        f"redirect_uri={urllib.parse.quote(_SITE_URL + '/api/twitch/viewer_auth')}",
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
                "redirect_uri": _SITE_URL + "/api/twitch/viewer_auth",
            },
        ) as resp:
            res = await resp.json()
            if "access_token" not in res:
                return None
            return res


async def refresh_auth_token(refresh_token: str) -> dict | None:
    """Exchange a refresh token for a fresh Twitch access token.

    Returns the full token response dict (contains access_token, expires_in,
    refresh_token, …) or None if the refresh request fails.
    """
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": os.getenv("THINVITE_TWITCH_ID"),
                "client_secret": os.getenv("THINVITE_TWITCH_SECRET"),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
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
    async with db._acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT twitch_redeem_id FROM users WHERE session_id = %s", (sess_id,)
            )
            res = await cur.fetchone()
            if res is not None and res[0] is not None:
                return res[0]
            return None


async def update_reward_queue_setting(
    sess_id: str, reward_id: str, skip_queue: bool
) -> bool:
    """Set should_redemptions_skip_request_queue on a custom reward."""
    if not sanitize.is_valid_uuid(reward_id):
        return False
    user = await get_user_from_db(sess_id)
    if user is None:
        return False
    broadcaster_id = user["twitch_user_id"]
    token = user["twitch_auth_token"]
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    ) as session:
        async with session.patch(
            f"https://api.twitch.tv/helix/channel_points/custom_rewards"
            f"?broadcaster_id={broadcaster_id}&id={reward_id}",
            json={"should_redemptions_skip_request_queue": skip_queue},
        ) as resp:
            if resp.status != 200:
                logger.error(
                    f"Failed to update reward queue setting: {resp.status} {await resp.text()}"
                )
                return False
            return True


async def cancel_redemption(
    broadcaster_id: str, reward_id: str, redemption_id: str, token: str
) -> bool:
    """Mark a channel point redemption as CANCELED (refunds the viewer's points)."""
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    ) as session:
        async with session.patch(
            f"https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions"
            f"?broadcaster_id={broadcaster_id}&reward_id={reward_id}&id={redemption_id}",
            json={"status": "CANCELED"},
        ) as resp:
            if resp.status != 200:
                logger.error(
                    f"Failed to cancel redemption {redemption_id}: {resp.status} {await resp.text()}"
                )
                return False
            return True


async def fulfill_redemption(
    streamer_sess_id: str, reward_id: str, redemption_id: str
) -> bool:
    """Mark a channel point redemption as FULFILLED via the Twitch API."""
    user = await db.get_user_by_session_id(streamer_sess_id)
    if user is None:
        return False
    broadcaster_id = user["twitch_user_id"]
    token = user["twitch_auth_token"]
    async with aiohttp.ClientSession(
        timeout=_TIMEOUT,
        headers={
            "client-id": os.getenv("THINVITE_TWITCH_ID"),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    ) as session:
        async with session.patch(
            f"https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions"
            f"?broadcaster_id={broadcaster_id}&reward_id={reward_id}&id={redemption_id}",
            json={"status": "FULFILLED"},
        ) as resp:
            if resp.status != 200:
                logger.error(
                    f"Failed to fulfill redemption {redemption_id}: {resp.status} {await resp.text()}"
                )
                return False
            return True


# ---------------------------------------------------------------------------
# Follow-age check (for extension panel)
# ---------------------------------------------------------------------------

async def get_follow_age(broadcaster_id: str, user_id: str, token: str) -> int | None:
    """Get how many minutes user_id has followed broadcaster_id.

    Returns minutes as int, or None if not following.
    Retries once with a refreshed token on 401.
    """
    from datetime import datetime, timezone

    url = (
        f"https://api.twitch.tv/helix/channels/followers"
        f"?broadcaster_id={broadcaster_id}&user_id={user_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Client-Id": os.environ["THINVITE_TWITCH_ID"],
    }

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                # Token expired — try to refresh and retry
                user = await db.get_user_by_twitch_id(broadcaster_id)
                if not user or not user.get("twitch_token_refresh_code"):
                    return None
                refreshed = await refresh_auth_token(user["twitch_token_refresh_code"])
                if not refreshed:
                    return None
                new_token = refreshed["access_token"]
                await db.update_twitch_auth_token(
                    user["session_id"], new_token,
                    refreshed["expires_in"] + int(time.time()),
                    refreshed["refresh_token"],
                )
                headers["Authorization"] = f"Bearer {new_token}"
                async with session.get(url, headers=headers) as retry_resp:
                    if retry_resp.status != 200:
                        return None
                    data = await retry_resp.json()
            elif resp.status != 200:
                logger.warning("get_follow_age failed: %s", resp.status)
                return None
            else:
                data = await resp.json()

    follows = data.get("data", [])
    if not follows:
        return None

    followed_at = datetime.fromisoformat(follows[0]["followed_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return int((now - followed_at).total_seconds() // 60)
