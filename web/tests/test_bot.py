"""Tests for bot.py — EventSub webhook subscription management."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import bot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user(
    sess_id="s1",
    twitch_user_id="b1",
    twitch_user_name="streamer1",
    discord_server_id="guild1",
    eventsub_subscription_id=None,
):
    return {
        "session_id": sess_id,
        "twitch_user_id": twitch_user_id,
        "twitch_user_name": twitch_user_name,
        "discord_server_id": discord_server_id,
        "eventsub_subscription_id": eventsub_subscription_id,
        "twitch_auth_token": "tok",
    }


@pytest.fixture(autouse=True)
def clear_subscriptions():
    """Reset the in-memory subscription set before each test."""
    bot._subscriptions.clear()
    yield
    bot._subscriptions.clear()


# ---------------------------------------------------------------------------
# has_active_subscription
# ---------------------------------------------------------------------------
def test_has_active_subscription_false_when_empty():
    assert bot.has_active_subscription("s1") is False


def test_has_active_subscription_true_after_add():
    bot._subscriptions.add("s1")
    assert bot.has_active_subscription("s1") is True


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------
async def test_subscribe_success():
    user = _user()
    with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value="apptoken")):
        with patch("bot.twitch_api.create_eventsub_subscription", AsyncMock(return_value="sub123")):
            with patch("bot.db.set_eventsub_subscription", AsyncMock()) as mock_set:
                result = await bot.subscribe(user)

    assert result is True
    assert bot.has_active_subscription("s1")
    mock_set.assert_called_once_with("s1", "sub123")


async def test_subscribe_missing_twitch_user_id_returns_false():
    user = _user(twitch_user_id=None)
    result = await bot.subscribe(user)
    assert result is False
    assert not bot.has_active_subscription("s1")


async def test_subscribe_missing_discord_server_id_returns_false():
    user = _user(discord_server_id=None)
    result = await bot.subscribe(user)
    assert result is False


async def test_subscribe_no_app_token_returns_false():
    user = _user()
    with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value=None)):
        result = await bot.subscribe(user)
    assert result is False
    assert not bot.has_active_subscription("s1")


async def test_subscribe_twitch_api_failure_returns_false():
    user = _user()
    with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value="apptoken")):
        with patch("bot.twitch_api.create_eventsub_subscription", AsyncMock(return_value=None)):
            result = await bot.subscribe(user)
    assert result is False
    assert not bot.has_active_subscription("s1")


# ---------------------------------------------------------------------------
# unsubscribe
# ---------------------------------------------------------------------------
async def test_unsubscribe_removes_from_set_and_calls_delete():
    bot._subscriptions.add("s1")
    user = _user(eventsub_subscription_id="sub123")

    with patch("bot.db.get_user_by_session_id", AsyncMock(return_value=user)):
        with patch("bot.db.clear_eventsub_subscription", AsyncMock()) as mock_clear:
            with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value="apptoken")):
                with patch("bot.twitch_api.delete_eventsub_subscription", AsyncMock(return_value=True)):
                    await bot.unsubscribe("s1")

    assert not bot.has_active_subscription("s1")
    mock_clear.assert_called_once_with("s1")


async def test_unsubscribe_no_user_is_silent():
    """Unsubscribing a session with no DB record should not raise."""
    with patch("bot.db.get_user_by_session_id", AsyncMock(return_value=None)):
        await bot.unsubscribe("nonexistent")  # must not raise


async def test_unsubscribe_no_sub_id_skips_delete():
    """If there's no stored sub_id, no Twitch DELETE is called."""
    user = _user(eventsub_subscription_id=None)
    with patch("bot.db.get_user_by_session_id", AsyncMock(return_value=user)):
        with patch("bot.twitch_api.delete_eventsub_subscription", AsyncMock()) as mock_del:
            await bot.unsubscribe("s1")
    mock_del.assert_not_called()


# ---------------------------------------------------------------------------
# handle_revocation
# ---------------------------------------------------------------------------
async def test_handle_revocation_clears_state():
    bot._subscriptions.add("s1")
    with patch("bot.db.clear_eventsub_subscription", AsyncMock()) as mock_clear:
        await bot.handle_revocation("s1")

    assert not bot.has_active_subscription("s1")
    mock_clear.assert_called_once_with("s1")


# ---------------------------------------------------------------------------
# recover_subscriptions
# ---------------------------------------------------------------------------
async def test_recover_subscriptions_no_users_exits_early():
    with patch("bot.db.get_all_bot_users", AsyncMock(return_value=[])):
        with patch("bot.twitch_api.get_app_access_token", AsyncMock()) as mock_tok:
            await bot.recover_subscriptions()
    mock_tok.assert_not_called()


async def test_recover_subscriptions_no_app_token_exits_early():
    users = [_user()]
    with patch("bot.db.get_all_bot_users", AsyncMock(return_value=users)):
        with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value=None)):
            with patch("bot.twitch_api.get_eventsub_subscription_status", AsyncMock()) as mock_stat:
                await bot.recover_subscriptions()
    mock_stat.assert_not_called()


async def test_recover_subscriptions_enabled_sub_added_to_set():
    user = _user(eventsub_subscription_id="sub123")
    with patch("bot.db.get_all_bot_users", AsyncMock(return_value=[user])):
        with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value="apptoken")):
            with patch("bot.twitch_api.get_eventsub_subscription_status",
                       AsyncMock(return_value="enabled")):
                await bot.recover_subscriptions()

    assert bot.has_active_subscription("s1")


async def test_recover_subscriptions_stale_sub_reregistered():
    """A stored sub with non-'enabled' status should be deleted and recreated."""
    user = _user(eventsub_subscription_id="old-sub")
    with patch("bot.db.get_all_bot_users", AsyncMock(return_value=[user])):
        with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value="apptoken")):
            with patch("bot.twitch_api.get_eventsub_subscription_status",
                       AsyncMock(return_value="notification_failures_exceeded")):
                with patch("bot.db.clear_eventsub_subscription", AsyncMock()) as mock_clear:
                    with patch("bot.twitch_api.create_eventsub_subscription",
                               AsyncMock(return_value="new-sub")) as mock_create:
                        with patch("bot.db.set_eventsub_subscription", AsyncMock()) as mock_set:
                            await bot.recover_subscriptions()

    mock_clear.assert_called_once_with("s1")
    mock_create.assert_called_once()
    mock_set.assert_called_once_with("s1", "new-sub")
    assert bot.has_active_subscription("s1")


async def test_recover_subscriptions_no_stored_sub_creates_one():
    """Users with no stored sub_id get a fresh subscription."""
    user = _user(eventsub_subscription_id=None)
    with patch("bot.db.get_all_bot_users", AsyncMock(return_value=[user])):
        with patch("bot.twitch_api.get_app_access_token", AsyncMock(return_value="apptoken")):
            with patch("bot.twitch_api.create_eventsub_subscription",
                       AsyncMock(return_value="new-sub")) as mock_create:
                with patch("bot.db.set_eventsub_subscription", AsyncMock()) as mock_set:
                    await bot.recover_subscriptions()

    mock_create.assert_called_once()
    mock_set.assert_called_once_with("s1", "new-sub")
    assert bot.has_active_subscription("s1")
