"""Tests for expiry.py — scheduled background tasks (redemption expiry + token refresh)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

import expiry


# ---------------------------------------------------------------------------
# expire_old_redemptions — happy path
# ---------------------------------------------------------------------------
async def test_expire_old_redemptions_no_expired_rows():
    """When there are no expired rows the function returns without errors."""
    with patch("expiry.db.get_expired_pending_redemptions", AsyncMock(return_value=[])):
        with patch("expiry.db.expire_redemption", AsyncMock()) as mock_expire:
            await expiry.expire_old_redemptions()
    mock_expire.assert_not_called()


async def test_expire_old_redemptions_cancels_each_on_twitch():
    """Each expired row must be cancelled on Twitch then marked expired in DB."""
    rows = [
        {
            "id": 1,
            "twitch_redemption_id": "rid1",
            "twitch_reward_id": "rwid1",
            "broadcaster_id": "b1",
            "token": "tok1",
        },
        {
            "id": 2,
            "twitch_redemption_id": "rid2",
            "twitch_reward_id": "rwid2",
            "broadcaster_id": "b2",
            "token": "tok2",
        },
    ]
    mock_cancel = AsyncMock(return_value=True)
    mock_expire = AsyncMock()

    with patch("expiry.db.get_expired_pending_redemptions", AsyncMock(return_value=rows)):
        with patch("expiry.twitch_api.cancel_redemption", mock_cancel):
            with patch("expiry.db.expire_redemption", mock_expire):
                await expiry.expire_old_redemptions()

    assert mock_cancel.call_count == 2
    mock_cancel.assert_any_call("b1", "rwid1", "rid1", "tok1")
    mock_cancel.assert_any_call("b2", "rwid2", "rid2", "tok2")

    assert mock_expire.call_count == 2
    mock_expire.assert_any_call(1)
    mock_expire.assert_any_call(2)


# ---------------------------------------------------------------------------
# expire_old_redemptions — resilience
# ---------------------------------------------------------------------------
async def test_expire_old_redemptions_marks_db_even_when_twitch_fails():
    """If the Twitch cancel call returns False, the row is still expired in DB."""
    rows = [
        {
            "id": 5,
            "twitch_redemption_id": "rid5",
            "twitch_reward_id": "rwid5",
            "broadcaster_id": "b5",
            "token": "tok5",
        }
    ]
    mock_expire = AsyncMock()

    with patch("expiry.db.get_expired_pending_redemptions", AsyncMock(return_value=rows)):
        with patch("expiry.twitch_api.cancel_redemption", AsyncMock(return_value=False)):
            with patch("expiry.db.expire_redemption", mock_expire):
                await expiry.expire_old_redemptions()

    mock_expire.assert_called_once_with(5)


async def test_expire_old_redemptions_marks_db_even_when_twitch_raises():
    """If the Twitch cancel raises an exception, the row is still expired in DB."""
    rows = [
        {
            "id": 7,
            "twitch_redemption_id": "rid7",
            "twitch_reward_id": "rwid7",
            "broadcaster_id": "b7",
            "token": "tok7",
        }
    ]
    mock_expire = AsyncMock()

    with patch("expiry.db.get_expired_pending_redemptions", AsyncMock(return_value=rows)):
        with patch("expiry.twitch_api.cancel_redemption", AsyncMock(side_effect=RuntimeError("boom"))):
            with patch("expiry.db.expire_redemption", mock_expire):
                await expiry.expire_old_redemptions()

    mock_expire.assert_called_once_with(7)


async def test_expire_old_redemptions_continues_after_single_db_expire_failure():
    """A DB failure on one row must not prevent processing of subsequent rows."""
    rows = [
        {"id": 10, "twitch_redemption_id": "r10", "twitch_reward_id": "rw10",
         "broadcaster_id": "b10", "token": "t10"},
        {"id": 11, "twitch_redemption_id": "r11", "twitch_reward_id": "rw11",
         "broadcaster_id": "b11", "token": "t11"},
    ]

    expire_calls = []

    async def flaky_expire(rid):
        expire_calls.append(rid)
        if rid == 10:
            raise RuntimeError("DB write failed")

    with patch("expiry.db.get_expired_pending_redemptions", AsyncMock(return_value=rows)):
        with patch("expiry.twitch_api.cancel_redemption", AsyncMock(return_value=True)):
            with patch("expiry.db.expire_redemption", side_effect=flaky_expire):
                await expiry.expire_old_redemptions()

    assert 10 in expire_calls
    assert 11 in expire_calls


async def test_expire_old_redemptions_handles_db_fetch_exception():
    """If fetching expired rows raises, the function logs and returns cleanly."""
    with patch("expiry.db.get_expired_pending_redemptions",
               AsyncMock(side_effect=RuntimeError("DB is down"))):
        with patch("expiry.db.expire_redemption", AsyncMock()) as mock_expire:
            # should not raise
            await expiry.expire_old_redemptions()

    mock_expire.assert_not_called()


# ---------------------------------------------------------------------------
# refresh_expiring_tokens
# ---------------------------------------------------------------------------
async def test_refresh_expiring_tokens_no_users():
    """When no tokens are expiring, nothing else is called."""
    with patch("expiry.db.get_users_with_expiring_tokens", AsyncMock(return_value=[])):
        with patch("expiry.twitch_api.refresh_auth_token", AsyncMock()) as mock_refresh:
            await expiry.refresh_expiring_tokens()
    mock_refresh.assert_not_called()


async def test_refresh_expiring_tokens_skips_active_listeners():
    """Users with an active bot listener are skipped (twitchAPI handles their refresh)."""
    users = [{"session_id": "s1", "twitch_token_refresh_code": "r1"}]
    with patch("expiry.db.get_users_with_expiring_tokens", AsyncMock(return_value=users)):
        with patch("expiry.bot.has_active_listener", return_value=True):
            with patch("expiry.twitch_api.refresh_auth_token", AsyncMock()) as mock_refresh:
                await expiry.refresh_expiring_tokens()
    mock_refresh.assert_not_called()


async def test_refresh_expiring_tokens_refreshes_inactive_users(mock_pool_factory):
    """Users without an active listener get their tokens refreshed and DB updated."""
    mock_pool_factory(rowcount=1)
    users = [{"session_id": "s1", "twitch_token_refresh_code": "r1"}]
    new_token = {"access_token": "newtok", "expires_in": 14400, "refresh_token": "newref"}

    with patch("expiry.db.get_users_with_expiring_tokens", AsyncMock(return_value=users)):
        with patch("expiry.bot.has_active_listener", return_value=False):
            with patch("expiry.twitch_api.refresh_auth_token", AsyncMock(return_value=new_token)):
                with patch("expiry.db.update_twitch_auth_token", AsyncMock()) as mock_update:
                    await expiry.refresh_expiring_tokens()

    mock_update.assert_called_once()
    call_args = mock_update.call_args[0]
    assert call_args[0] == "s1"
    assert call_args[1] == "newtok"
    assert call_args[3] == "newref"


async def test_refresh_expiring_tokens_handles_failed_refresh():
    """If refresh_auth_token returns None, DB is not updated and loop continues."""
    users = [
        {"session_id": "s1", "twitch_token_refresh_code": "r1"},
        {"session_id": "s2", "twitch_token_refresh_code": "r2"},
    ]

    refresh_results = [None, {"access_token": "tok2", "expires_in": 14400, "refresh_token": "ref2"}]

    with patch("expiry.db.get_users_with_expiring_tokens", AsyncMock(return_value=users)):
        with patch("expiry.bot.has_active_listener", return_value=False):
            with patch("expiry.twitch_api.refresh_auth_token",
                       AsyncMock(side_effect=refresh_results)):
                with patch("expiry.db.update_twitch_auth_token", AsyncMock()) as mock_update:
                    await expiry.refresh_expiring_tokens()

    # Only s2 should be updated (s1 refresh failed)
    assert mock_update.call_count == 1
    assert mock_update.call_args[0][0] == "s2"


async def test_refresh_expiring_tokens_handles_db_fetch_exception():
    """If fetching expiring users raises, the function returns cleanly."""
    with patch("expiry.db.get_users_with_expiring_tokens",
               AsyncMock(side_effect=RuntimeError("DB down"))):
        with patch("expiry.twitch_api.refresh_auth_token", AsyncMock()) as mock_refresh:
            await expiry.refresh_expiring_tokens()
    mock_refresh.assert_not_called()
