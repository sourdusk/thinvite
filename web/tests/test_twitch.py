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

"""Tests for twitch.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import twitch
from tests.conftest import make_aiohttp_response, make_aiohttp_session


# ---------------------------------------------------------------------------
# URL generators (pure functions — no mocking needed)
# ---------------------------------------------------------------------------
def test_generate_auth_code_link_contains_scope():
    url = twitch.generate_auth_code_link("mystate")
    assert "user%3Awrite%3Achat" in url or "user:write:chat" in url
    assert "channel%3Aread%3Aredemptions" in url or "channel:read:redemptions" in url
    assert "channel%3Amanage%3Aredemptions" in url or "channel:manage:redemptions" in url
    assert "state=mystate" in url
    assert "force_verify=false" in url


def test_generate_auth_code_link_force_verify():
    url = twitch.generate_auth_code_link("s", force_verify=True)
    assert "force_verify=true" in url


def test_generate_viewer_auth_link_minimal_scope():
    url = twitch.generate_viewer_auth_link("viewerstate")
    assert "user%3Aread%3Aemail" in url or "user:read:email" in url
    assert "channel:read:redemptions" not in url
    assert "force_verify=true" in url
    assert "state=viewerstate" in url


# ---------------------------------------------------------------------------
# get_auth_token
# ---------------------------------------------------------------------------
async def test_get_auth_token_success():
    payload = {"access_token": "tok", "expires_in": 3600, "refresh_token": "ref"}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_auth_token("code123")
    assert result["access_token"] == "tok"


async def test_get_auth_token_missing_key_returns_none():
    resp = make_aiohttp_response({"error": "bad_code"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_auth_token("bad_code")
    assert result is None


# ---------------------------------------------------------------------------
# get_user_info
# ---------------------------------------------------------------------------
async def test_get_user_info_success():
    payload = {"data": [{"id": "u1", "login": "streamer1"}]}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_user_info("access_token")
    assert result["id"] == "u1"
    assert result["login"] == "streamer1"


async def test_get_user_info_empty_data_returns_none():
    resp = make_aiohttp_response({"data": []})
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_user_info("token")
    assert result is None


# ---------------------------------------------------------------------------
# user_exists
# ---------------------------------------------------------------------------
async def test_user_exists_true(mock_pool_factory):
    mock_pool_factory(fetchone={"session_id": "s", "twitch_user_id": "123"})
    assert await twitch.user_exists("s") is True


async def test_user_exists_no_twitch_id(mock_pool_factory):
    mock_pool_factory(fetchone={"session_id": "s", "twitch_user_id": None})
    assert await twitch.user_exists("s") is False


async def test_user_exists_no_record(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    assert await twitch.user_exists("s") is False


# ---------------------------------------------------------------------------
# get_viewer_token
# ---------------------------------------------------------------------------
async def test_get_viewer_token_success():
    payload = {"access_token": "vtok", "expires_in": 100, "refresh_token": "vref"}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_viewer_token("vcode")
    assert result["access_token"] == "vtok"


async def test_get_viewer_token_failure_returns_none():
    resp = make_aiohttp_response({"error": "invalid"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_viewer_token("bad")
    assert result is None


# ---------------------------------------------------------------------------
# revoke_token — fire-and-forget, just verify it calls the right endpoint
# ---------------------------------------------------------------------------
async def test_revoke_token_posts_to_correct_url():
    # revoke_token uses `await session.post(...)` directly (not as context manager),
    # so post must be an AsyncMock so it is awaitable.
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.post = AsyncMock()

    with patch("aiohttp.ClientSession", return_value=sess):
        await twitch.revoke_token("mytok")

    sess.post.assert_called_once()
    url = sess.post.call_args[0][0]
    assert "revoke" in url


# ---------------------------------------------------------------------------
# init_login — integration of get_auth_token + get_user_info + update_twitch_info
# ---------------------------------------------------------------------------
async def test_init_login_success(mock_pool):
    _, cur = mock_pool
    cur.rowcount = 1

    token_payload = {"access_token": "t", "expires_in": 3600, "refresh_token": "r"}
    user_payload = {"data": [{"id": "u1", "login": "user1"}]}

    token_resp = make_aiohttp_response(token_payload)
    user_resp = make_aiohttp_response(user_payload)

    token_sess = make_aiohttp_session(post_resp=token_resp)
    user_sess = make_aiohttp_session(get_resp=user_resp)

    sessions = iter([token_sess, user_sess])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await twitch.init_login("sess", "code")

    assert ok is True


async def test_init_login_bad_token():
    resp = make_aiohttp_response({"error": "bad"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        ok, msg = await twitch.init_login("sess", "code")
    assert ok is False
    assert "auth token" in msg


async def test_init_login_user_info_fails(mock_pool):
    """Token exchange succeeds but get_user_info returns empty data → (False, …)."""
    _, cur = mock_pool
    cur.rowcount = 1

    token_payload = {"access_token": "t", "expires_in": 3600, "refresh_token": "r"}
    token_resp = make_aiohttp_response(token_payload)
    user_resp = make_aiohttp_response({"data": []})  # empty → get_user_info returns None

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await twitch.init_login("sess", "code")

    assert ok is False
    assert "user info" in msg.lower()


# ---------------------------------------------------------------------------
# twitch.update_twitch_redeem  (UUID validation + DB write)
# ---------------------------------------------------------------------------
async def test_twitch_update_redeem_valid_uuid(mock_pool):
    _, cur = mock_pool
    cur.rowcount = 1
    result = await twitch.update_twitch_redeem("sess", "550e8400-e29b-41d4-a716-446655440000")
    assert result is True


async def test_twitch_update_redeem_invalid_uuid_rejected(mock_pool):
    _, cur = mock_pool
    result = await twitch.update_twitch_redeem("sess", "not-a-uuid")
    assert result is False
    cur.execute.assert_not_called()


async def test_twitch_update_redeem_empty_id_rejected(mock_pool):
    _, cur = mock_pool
    result = await twitch.update_twitch_redeem("sess", "")
    assert result is False
    cur.execute.assert_not_called()


async def test_twitch_update_redeem_rowcount_zero(mock_pool):
    _, cur = mock_pool
    cur.rowcount = 0
    result = await twitch.update_twitch_redeem("sess", "550e8400-e29b-41d4-a716-446655440000")
    assert result is False


# ---------------------------------------------------------------------------
# lookup_user_by_name  (username validation + DB lookup + Twitch API)
# ---------------------------------------------------------------------------
async def test_lookup_user_by_name_invalid_username_rejected(mock_pool):
    _, cur = mock_pool
    result = await twitch.lookup_user_by_name("sess", "<script>alert(1)</script>")
    assert result is None
    cur.execute.assert_not_called()


async def test_lookup_user_by_name_no_db_user(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await twitch.lookup_user_by_name("sess", "validuser")
    assert result is None


async def test_lookup_user_by_name_success(mock_pool_factory):
    mock_pool_factory(fetchone={"session_id": "sess", "twitch_auth_token": "tok123"})
    payload = {"data": [{"id": "u1", "login": "validuser"}]}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.lookup_user_by_name("sess", "validuser")
    assert result["login"] == "validuser"
    assert result["id"] == "u1"


async def test_lookup_user_by_name_not_on_twitch(mock_pool_factory):
    mock_pool_factory(fetchone={"session_id": "sess", "twitch_auth_token": "tok123"})
    resp = make_aiohttp_response({"data": []})
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.lookup_user_by_name("sess", "ghostuser")
    assert result is None


# ---------------------------------------------------------------------------
# get_set_redeem
# ---------------------------------------------------------------------------
async def test_get_set_redeem_found(mock_pool):
    _, cur = mock_pool
    cur.fetchone = AsyncMock(return_value=("550e8400-e29b-41d4-a716-446655440000",))
    result = await twitch.get_set_redeem("sess")
    assert result == "550e8400-e29b-41d4-a716-446655440000"


async def test_get_set_redeem_not_set(mock_pool):
    _, cur = mock_pool
    cur.fetchone = AsyncMock(return_value=(None,))
    result = await twitch.get_set_redeem("sess")
    assert result is None


async def test_get_set_redeem_no_row(mock_pool):
    _, cur = mock_pool
    cur.fetchone = AsyncMock(return_value=None)
    result = await twitch.get_set_redeem("sess")
    assert result is None


# ---------------------------------------------------------------------------
# get_channel_redeems  (returns full reward object list)
# ---------------------------------------------------------------------------
async def test_get_channel_redeems_no_user(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await twitch.get_channel_redeems("sess")
    assert result is None


async def test_get_channel_redeems_success(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    reward = {
        "id": "rid1",
        "title": "Discord Invite",
        "should_redemptions_skip_request_queue": False,
    }
    payload = {"data": [reward]}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_channel_redeems("sess")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == "rid1"
    assert result[0]["title"] == "Discord Invite"


async def test_get_channel_redeems_empty_data(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    resp = make_aiohttp_response({"data": []})
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_channel_redeems("sess")
    assert result == []


# ---------------------------------------------------------------------------
# update_reward_queue_setting
# ---------------------------------------------------------------------------
async def test_update_reward_queue_invalid_uuid_rejected(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    result = await twitch.update_reward_queue_setting("sess", "not-a-uuid", False)
    assert result is False


async def test_update_reward_queue_no_user(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await twitch.update_reward_queue_setting(
        "sess", "550e8400-e29b-41d4-a716-446655440000", False
    )
    assert result is False


async def test_update_reward_queue_success(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    resp = make_aiohttp_response({}, status=200)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.patch = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.update_reward_queue_setting(
            "sess", "550e8400-e29b-41d4-a716-446655440000", False
        )
    assert result is True


async def test_update_reward_queue_failure(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    resp = make_aiohttp_response({"error": "Forbidden"}, status=403)
    resp.text = AsyncMock(return_value="Forbidden")
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.patch = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.update_reward_queue_setting(
            "sess", "550e8400-e29b-41d4-a716-446655440000", False
        )
    assert result is False


# ---------------------------------------------------------------------------
# cancel_redemption
# ---------------------------------------------------------------------------
async def test_cancel_redemption_success():
    resp = make_aiohttp_response({}, status=200)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.patch = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.cancel_redemption("bid", "rwid", "rdid", "token")
    assert result is True
    call_kwargs = sess.patch.call_args[1]
    assert call_kwargs["json"]["status"] == "CANCELED"


async def test_cancel_redemption_failure():
    resp = make_aiohttp_response({"error": "not found"}, status=404)
    resp.text = AsyncMock(return_value="not found")
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.patch = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.cancel_redemption("bid", "rwid", "rdid", "token")
    assert result is False


# ---------------------------------------------------------------------------
# fulfill_redemption
# ---------------------------------------------------------------------------
async def test_fulfill_redemption_no_user(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await twitch.fulfill_redemption("sess", "rw-id", "rd-id")
    assert result is False


async def test_fulfill_redemption_success(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    resp = make_aiohttp_response({}, status=200)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.patch = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.fulfill_redemption("sess", "rw-id", "rd-id")
    assert result is True
    call_kwargs = sess.patch.call_args[1]
    assert call_kwargs["json"]["status"] == "FULFILLED"


# ---------------------------------------------------------------------------
# refresh_auth_token
# ---------------------------------------------------------------------------
async def test_refresh_auth_token_success():
    payload = {"access_token": "newtok", "expires_in": 14400, "refresh_token": "newref"}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.refresh_auth_token("oldref")
    assert result["access_token"] == "newtok"
    assert result["refresh_token"] == "newref"


async def test_refresh_auth_token_failure_returns_none():
    resp = make_aiohttp_response({"status": 400, "message": "Invalid refresh token"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.refresh_auth_token("badref")
    assert result is None


# ---------------------------------------------------------------------------
# get_app_access_token
# ---------------------------------------------------------------------------
async def test_get_app_access_token_success():
    twitch._app_token = None
    twitch._app_token_expiry = 0.0
    payload = {"access_token": "apptoken123", "expires_in": 3600}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_app_access_token()
    assert result == "apptoken123"
    assert twitch._app_token == "apptoken123"


async def test_get_app_access_token_failure_returns_none():
    twitch._app_token = None
    twitch._app_token_expiry = 0.0
    resp = make_aiohttp_response({"error": "invalid_client"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_app_access_token()
    assert result is None


async def test_get_app_access_token_uses_cache():
    import time
    twitch._app_token = "cached"
    twitch._app_token_expiry = time.time() + 3900  # well within expiry window
    with patch("aiohttp.ClientSession") as mock_sess:
        result = await twitch.get_app_access_token()
    assert result == "cached"
    mock_sess.assert_not_called()


# ---------------------------------------------------------------------------
# create_eventsub_subscription
# ---------------------------------------------------------------------------
async def test_create_eventsub_subscription_success():
    payload = {"data": [{"id": "sub999"}]}
    resp = make_aiohttp_response(payload, status=202)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.create_eventsub_subscription(
            "bid1", "https://cb.example.com/eventsub/callback", "secret", "apptoken"
        )
    assert result == "sub999"


async def test_create_eventsub_subscription_failure_returns_none():
    resp = make_aiohttp_response({"error": "Conflict"}, status=409)
    resp.text = AsyncMock(return_value="Conflict")
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.create_eventsub_subscription(
            "bid1", "https://cb.example.com/eventsub/callback", "secret", "apptoken"
        )
    assert result is None


# ---------------------------------------------------------------------------
# delete_eventsub_subscription
# ---------------------------------------------------------------------------
async def test_delete_eventsub_subscription_success():
    resp = make_aiohttp_response({}, status=204)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.delete = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.delete_eventsub_subscription("sub123", "apptoken")
    assert result is True


async def test_delete_eventsub_subscription_404_returns_false():
    resp = make_aiohttp_response({}, status=404)
    resp.text = AsyncMock(return_value="Not Found")
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.delete = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.delete_eventsub_subscription("sub123", "apptoken")
    assert result is False


# ---------------------------------------------------------------------------
# get_eventsub_subscription_status
# ---------------------------------------------------------------------------
async def test_get_eventsub_subscription_status_enabled():
    payload = {"data": [{"id": "sub123", "status": "enabled"}]}
    resp = make_aiohttp_response(payload, status=200)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_eventsub_subscription_status("sub123", "apptoken")
    assert result == "enabled"


async def test_get_eventsub_subscription_status_not_found_returns_none():
    resp = make_aiohttp_response({"data": []}, status=200)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_eventsub_subscription_status("sub999", "apptoken")
    assert result is None


async def test_get_eventsub_subscription_status_error_returns_none():
    resp = make_aiohttp_response({}, status=401)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_eventsub_subscription_status("sub123", "apptoken")
    assert result is None


# ---------------------------------------------------------------------------
# send_chat_message
# ---------------------------------------------------------------------------
async def test_send_chat_message_success():
    resp = make_aiohttp_response({}, status=200)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.send_chat_message("b1", "b1", "Hello!", "token")
    assert result is True


async def test_send_chat_message_failure_returns_false():
    resp = make_aiohttp_response({"error": "Forbidden"}, status=403)
    resp.text = AsyncMock(return_value="Forbidden")
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.send_chat_message("b1", "b1", "Hello!", "token")
    assert result is False
