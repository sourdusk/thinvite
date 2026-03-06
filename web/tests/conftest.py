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

"""Shared fixtures for the Thinvite test suite."""
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Environment — ensure env vars are present so module-level os.getenv() calls
# don't leave None values that could cause string-formatting errors.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("THINVITE_TWITCH_ID", "test_twitch_id")
    monkeypatch.setenv("THINVITE_TWITCH_SECRET", "test_twitch_secret")
    monkeypatch.setenv("THINVITE_DISCORD_ID", "test_discord_id")
    monkeypatch.setenv("THINVITE_DISCORD_SECRET", "test_discord_secret")
    monkeypatch.setenv("THINVITE_DISCORD_BOT_TOKEN", "test_bot_token")
    monkeypatch.setenv("THINVITE_DB_PASSWORD", "test_db_password")
    monkeypatch.setenv("MAILJET_API_KEY", "test_mj_key")
    monkeypatch.setenv("MAILJET_SECRET_KEY", "test_mj_secret")
    monkeypatch.setenv("MAILJET_SENDER_EMAIL", "noreply@test.thinvite.com")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "test_site_key")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test_turnstile_secret")
    monkeypatch.setenv("NICEGUI_STORAGE_SECRET", "test_storage_secret")
    monkeypatch.setenv("THINVITE_EVENTSUB_SECRET", "test_eventsub_secret")
    monkeypatch.setenv("SITE_URL", "https://test.thinvite.com")


# ---------------------------------------------------------------------------
# DB pool mock — patches db._pool so no real MySQL connection is needed.
# Returns (pool_mock, cursor_mock).  Tests can adjust cursor_mock attributes
# (fetchone, fetchall, rowcount) to control return values.
# ---------------------------------------------------------------------------
def _build_pool_mock(fetchone=None, fetchall=None, rowcount=1):
    cur = AsyncMock()
    cur.rowcount = rowcount
    cur.fetchone = AsyncMock(return_value=fetchone)
    cur.fetchall = AsyncMock(return_value=fetchall or [])
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=False)

    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool, cur


@pytest.fixture
def mock_pool(monkeypatch):
    """Patch db._pool and return (pool, cursor)."""
    import db
    pool, cur = _build_pool_mock()
    monkeypatch.setattr(db, "_pool", pool)
    return pool, cur


@pytest.fixture
def mock_pool_factory(monkeypatch):
    """Return a callable that creates and installs a pool with custom defaults."""
    import db

    def _factory(fetchone=None, fetchall=None, rowcount=1):
        pool, cur = _build_pool_mock(fetchone=fetchone, fetchall=fetchall, rowcount=rowcount)
        monkeypatch.setattr(db, "_pool", pool)
        return pool, cur

    return _factory


# ---------------------------------------------------------------------------
# aiohttp session mock helper
# ---------------------------------------------------------------------------
def make_aiohttp_response(json_data: dict, status: int = 200):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=str(json_data))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def make_aiohttp_session(get_resp=None, post_resp=None):
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    if get_resp is not None:
        sess.get = MagicMock(return_value=get_resp)
    if post_resp is not None:
        sess.post = MagicMock(return_value=post_resp)
    return sess
