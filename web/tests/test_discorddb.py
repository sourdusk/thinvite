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
# get_user_guilds
# ---------------------------------------------------------------------------
async def test_get_user_guilds_success():
    guilds_data = [{"id": "123456789012345678", "owner": True, "permissions": "0"}]
    resp = make_aiohttp_response(guilds_data)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.get_user_guilds("tok")
    assert result == guilds_data


async def test_get_user_guilds_non_200_returns_none():
    resp = make_aiohttp_response({}, status=401)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await discorddb.get_user_guilds("bad_tok")
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
    """Happy path: token exchange → guild ownership check → user info → DB update all succeed."""
    _, cur = mock_pool
    cur.rowcount = 1

    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([{"id": "123456789012345678", "owner": True, "permissions": "0"}])
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),   # get_discord_token
        make_aiohttp_session(get_resp=guilds_resp),   # get_user_guilds
        make_aiohttp_session(get_resp=user_resp),     # get_user_info
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is True
    assert msg == "Success"


async def test_update_info_db_update_fails(mock_pool):
    """rowcount != 1 after UPDATE → (False, …)."""
    _, cur = mock_pool
    cur.rowcount = 0

    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([{"id": "123456789012345678", "owner": True, "permissions": "0"}])
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),   # get_discord_token
        make_aiohttp_session(get_resp=guilds_resp),   # get_user_guilds
        make_aiohttp_session(get_resp=user_resp),     # get_user_info
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is False


async def test_update_info_guild_not_owned_rejected():
    """Attacker supplies a guild_id they don't own — must be rejected before any DB write."""
    token_resp = make_aiohttp_response({"access_token": "tok"})
    # Attacker owns a completely different guild, not the target
    guilds_resp = make_aiohttp_response([{"id": "999999999999999999", "owner": True, "permissions": "0"}])

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),   # get_discord_token
        make_aiohttp_session(get_resp=guilds_resp),   # get_user_guilds
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is False
    assert "permission" in msg.lower()


async def test_update_info_guilds_fetch_fails_rejected():
    """If Discord's guild list endpoint is unavailable, registration fails closed."""
    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response({}, status=500)

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),   # get_discord_token
        make_aiohttp_session(get_resp=guilds_resp),   # get_user_guilds (fails)
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is False
    assert "verify" in msg.lower()


# ---------------------------------------------------------------------------
# update_info — guild permission bit variants
# ---------------------------------------------------------------------------
async def test_update_info_manage_guild_permission_accepted(mock_pool):
    """A non-owner with MANAGE_GUILD (0x20 = 32) must be allowed."""
    _, cur = mock_pool
    cur.rowcount = 1

    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([{
        "id": "123456789012345678", "owner": False, "permissions": "32",  # MANAGE_GUILD
    }])
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=guilds_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, _ = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is True


async def test_update_info_administrator_permission_accepted(mock_pool):
    """A non-owner with ADMINISTRATOR (0x8 = 8) must be allowed."""
    _, cur = mock_pool
    cur.rowcount = 1

    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([{
        "id": "123456789012345678", "owner": False, "permissions": "8",  # ADMINISTRATOR
    }])
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=guilds_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, _ = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is True


async def test_update_info_member_no_permissions_rejected():
    """A guild member with zero permissions must be rejected."""
    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([{
        "id": "123456789012345678", "owner": False, "permissions": "0",
    }])

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=guilds_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is False
    assert "permission" in msg.lower()


async def test_update_info_empty_guild_list_rejected():
    """A token with no guilds must be rejected."""
    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([])  # empty list

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=guilds_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, msg = await discorddb.update_info("sess", "code", "123456789012345678")

    assert ok is False
    assert "permission" in msg.lower()


# ---------------------------------------------------------------------------
# update_info — regression: no UPDATE by guild_id (account-hijack prevention)
# ---------------------------------------------------------------------------
async def test_update_info_only_updates_by_session_id(mock_pool):
    """The DB write must target only the current session row, never by guild_id.

    Regression test for the account-hijack vulnerability where a caller could
    supply a victim's guild_id and have their session_id written into the
    victim's row via 'UPDATE users SET session_id = ? WHERE discord_server_id = ?'.
    """
    _, cur = mock_pool
    cur.rowcount = 1

    guild_id = "123456789012345678"
    token_resp = make_aiohttp_response({"access_token": "tok"})
    guilds_resp = make_aiohttp_response([{"id": guild_id, "owner": True, "permissions": "0"}])
    user_resp = make_aiohttp_response({"id": "uid123"})

    sessions = iter([
        make_aiohttp_session(post_resp=token_resp),
        make_aiohttp_session(get_resp=guilds_resp),
        make_aiohttp_session(get_resp=user_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        ok, _ = await discorddb.update_info("my_session", "code", guild_id)

    assert ok is True
    # Exactly one DB execute call — no extra UPDATE by guild_id
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    # The WHERE predicate must use the session_id value
    assert params[-1] == "my_session", "WHERE clause must bind the session_id"
    # The WHERE clause must not reference discord_server_id
    where_part = sql.split("WHERE")[-1]
    assert "discord_server_id" not in where_part, \
        "WHERE clause must not contain discord_server_id (account-hijack vector)"


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

