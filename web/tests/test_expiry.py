"""Tests for expiry.py — 24-hour pending redemption auto-cancel."""
import pytest
from unittest.mock import AsyncMock, patch, call

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
