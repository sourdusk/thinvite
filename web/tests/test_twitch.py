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
    assert "user:write:chat" in url
    assert "channel:read:redemptions" in url
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
# get_channel_redeems
# ---------------------------------------------------------------------------
async def test_get_channel_redeems_no_user(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await twitch.get_channel_redeems("sess")
    assert result is None


async def test_get_channel_redeems_success(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    payload = {"data": [{"id": "rid1", "title": "Discord Invite"}]}
    resp = make_aiohttp_response(payload)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_channel_redeems("sess")
    assert result == {"rid1": "Discord Invite"}


async def test_get_channel_redeems_empty_data(mock_pool_factory):
    mock_pool_factory(fetchone={"twitch_user_id": "u1", "twitch_auth_token": "tok"})
    resp = make_aiohttp_response({"data": []})
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await twitch.get_channel_redeems("sess")
    assert result == {}
