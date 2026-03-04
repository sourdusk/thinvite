"""Tests for discorddb.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discorddb
from tests.conftest import make_aiohttp_response, make_aiohttp_session


# ---------------------------------------------------------------------------
# get_discord_token
# ---------------------------------------------------------------------------
async def test_get_discord_token_success():
    resp = make_aiohttp_response({"access_token": "discord_tok"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.get_discord_token("code")
    assert result == "discord_tok"


async def test_get_discord_token_failure_returns_none():
    resp = make_aiohttp_response({"error": "invalid_grant"})
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.get_discord_token("bad_code")
    assert result is None


# ---------------------------------------------------------------------------
# get_user_info
# ---------------------------------------------------------------------------
async def test_get_user_info_success():
    resp = make_aiohttp_response({"id": "discord_user_id", "username": "testuser"})
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.get_user_info("bearer_token")
    assert result == "discord_user_id"


async def test_get_user_info_no_id_returns_none():
    resp = make_aiohttp_response({"error": "401: unauthorized"})
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.get_user_info("bad_token")
    assert result is None


# ---------------------------------------------------------------------------
# create_invite
# ---------------------------------------------------------------------------
def _make_channel_invite_session(channels, invite_code="abc123", invite_status=200):
    """Build a session mock that serves GET /channels then POST /invites."""
    channels_resp = make_aiohttp_response(channels, status=200)
    invite_resp = make_aiohttp_response({"code": invite_code}, status=invite_status)

    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.get = MagicMock(return_value=channels_resp)
    sess.post = MagicMock(return_value=invite_resp)
    return sess


_VALID_GUILD = "123456789012345678"


async def test_create_invite_success():
    channels = [{"id": "ch1", "type": 0, "name": "general"}]
    sess = _make_channel_invite_session(channels, invite_code="xyz789")
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.create_invite(_VALID_GUILD)
    assert result == "https://discord.gg/xyz789"


async def test_create_invite_no_text_channel_returns_none():
    # Only a voice channel (type=2), no text channels
    channels = [{"id": "vc1", "type": 2, "name": "voice"}]
    sess = _make_channel_invite_session(channels)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.create_invite(_VALID_GUILD)
    assert result is None


async def test_create_invite_channel_fetch_fails_returns_none():
    resp = make_aiohttp_response({}, status=403)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.get = MagicMock(return_value=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.create_invite(_VALID_GUILD)
    assert result is None


async def test_create_invite_post_fails_returns_none():
    channels = [{"id": "ch1", "type": 0}]
    sess = _make_channel_invite_session(channels, invite_status=403)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.create_invite(_VALID_GUILD)
    assert result is None


# ---------------------------------------------------------------------------
# No sensitive data leaks to logs (regression)
# ---------------------------------------------------------------------------
async def test_update_info_no_token_logged(mock_pool, caplog):
    """Ensures the bearer token is not emitted at any log level."""
    import logging

    # Arrange: Discord token exchange succeeds, user info succeeds,
    # DB update succeeds (rowcount=1)
    _, cur = mock_pool
    cur.fetchone = AsyncMock(return_value=None)  # no existing guild
    cur.rowcount = 1

    token_resp = make_aiohttp_response({"access_token": "super_secret_token"})
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])

    with caplog.at_level(logging.DEBUG):
        with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
            await discorddb.update_info("sess", "code", "guild_id")

    assert "super_secret_token" not in caplog.text


# ---------------------------------------------------------------------------
# update_info — input validation + success path
# ---------------------------------------------------------------------------
async def test_update_info_invalid_guild_id_rejected():
    """Non-snowflake guild_id must be rejected before any network call."""
    ok, msg = await discorddb.update_info("sess", "code", "not-a-snowflake")
    assert ok is False
    assert "Invalid guild ID" in msg


async def test_update_info_success(mock_pool):
    """Happy path: token exchange → user info → DB update all succeed."""
    _, cur = mock_pool
    cur.fetchone = AsyncMock(return_value=None)  # no pre-existing guild row
    cur.rowcount = 1

    token_resp = make_aiohttp_response({"access_token": "tok"})
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is True
    assert msg == "Success"


async def test_update_info_db_update_fails(mock_pool):
    """rowcount != 1 after UPDATE → (False, …)."""
    _, cur = mock_pool
    cur.fetchone = AsyncMock(return_value=None)
    cur.rowcount = 0

    token_resp = make_aiohttp_response({"access_token": "tok"})
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is False


# ---------------------------------------------------------------------------
# create_invite — snowflake validation
# ---------------------------------------------------------------------------
async def test_create_invite_invalid_snowflake_rejected():
    """Non-snowflake guild_id must return None without any HTTP call."""
    result = await discorddb.create_invite("not-a-snowflake")
    assert result is None


async def test_create_invite_empty_snowflake_rejected():
    result = await discorddb.create_invite("")
    assert result is None
