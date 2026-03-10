import base64
import time
from unittest.mock import patch, AsyncMock, MagicMock

import jwt
import pytest

# Suppress NiceGUI startup before importing main
from nicegui import app as _nicegui_app, ui as _nicegui_ui

_ui_run_patcher = patch.object(_nicegui_ui, "run")
_ui_run_patcher.start()

_static_patcher = patch.object(_nicegui_app, "add_static_files")
_static_patcher.start()

import main  # noqa: E402

_TEST_SECRET = b"test-secret-key-for-unit-tests!!"


def _make_jwt(user_id="99999", channel_id="11111", role="viewer"):
    return jwt.encode(
        {"user_id": user_id, "channel_id": channel_id, "role": role,
         "exp": int(time.time()) + 300},
        _TEST_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TWITCH_EXT_SECRET", base64.b64encode(_TEST_SECRET).decode())
    monkeypatch.setenv("TWITCH_EXT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_EXT_OWNER_ID", "12345")
    import ext_auth
    ext_auth._secret_bytes = None
    main._follow_age_cache.clear()


async def test_ext_status_not_configured():
    """Returns error when streamer hasn't configured extension."""
    with patch("db.get_ext_config", new_callable=AsyncMock, return_value=None):
        result = await main._ext_get_status(user_id="99999", channel_id="11111")
    assert result["error"] == "not_configured"


async def test_ext_status_eligible():
    """Returns eligible when viewer meets follow-age requirement."""
    config = {
        "session_id": "sess-1", "discord_server_id": "guild-1",
        "ext_min_follow_days": 30, "ext_cooldown_days": 14,
    }
    user_row = {"twitch_auth_token": "tok-1", "session_id": "sess-1"}
    with patch("db.get_ext_config", new_callable=AsyncMock, return_value=config), \
         patch("db.get_pending_redemptions_for_viewer", new_callable=AsyncMock, return_value=[]), \
         patch("db.has_recent_invite", new_callable=AsyncMock, return_value=False), \
         patch("db.get_user_by_twitch_id", new_callable=AsyncMock, return_value=user_row), \
         patch("twitch.get_follow_age", new_callable=AsyncMock, return_value=60):
        result = await main._ext_get_status(user_id="99999", channel_id="11111")
    assert result["follow_age_eligible"] is True
    assert result["follow_age_days"] == 60


async def test_ext_status_pending_redemption():
    """Returns has_pending_redemption when viewer has a pending channel-point claim."""
    config = {
        "session_id": "sess-1", "discord_server_id": "guild-1",
        "ext_min_follow_days": 30, "ext_cooldown_days": 14,
    }
    pending = [{"id": 1, "streamer_session_id": "sess-1"}]
    user_row = {"twitch_auth_token": "tok-1", "session_id": "sess-1"}
    with patch("db.get_ext_config", new_callable=AsyncMock, return_value=config), \
         patch("db.get_pending_redemptions_for_viewer", new_callable=AsyncMock, return_value=pending), \
         patch("db.has_recent_invite", new_callable=AsyncMock, return_value=False), \
         patch("db.get_user_by_twitch_id", new_callable=AsyncMock, return_value=user_row), \
         patch("twitch.get_follow_age", new_callable=AsyncMock, return_value=60):
        result = await main._ext_get_status(user_id="99999", channel_id="11111")
    assert result["has_pending_redemption"] is True
