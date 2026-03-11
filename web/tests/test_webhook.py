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

"""Tests for the EventSub webhook handler helpers in main.py.

main.py calls ui.run() at module level and app.add_static_files() — both
are patched before import, exactly as in test_main_helpers.py.
"""
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui import app as _nicegui_app, ui as _nicegui_ui

_ui_run_patcher = patch.object(_nicegui_ui, "run")
_ui_run_patcher.start()

_static_patcher = patch.object(_nicegui_app, "add_static_files")
_static_patcher.start()

import main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SECRET = "test_eventsub_secret"


def _make_sig(msg_id: str, timestamp: str, body: bytes) -> str:
    message = (msg_id + timestamp).encode() + body
    return "sha256=" + hmac.new(_SECRET.encode(), message, hashlib.sha256).hexdigest()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# _verify_eventsub_signature
# ---------------------------------------------------------------------------
def test_signature_valid():
    body = b'{"foo": "bar"}'
    ts = _now_iso()
    sig = _make_sig("msg1", ts, body)
    main._EVENTSUB_SECRET = _SECRET
    assert main._verify_eventsub_signature("msg1", ts, sig, body) is True


def test_signature_invalid():
    body = b'{"foo": "bar"}'
    ts = _now_iso()
    main._EVENTSUB_SECRET = _SECRET
    assert main._verify_eventsub_signature("msg1", ts, "sha256=badhash", body) is False


def test_signature_tampered_body():
    body = b'{"foo": "bar"}'
    tampered = b'{"foo": "evil"}'
    ts = _now_iso()
    sig = _make_sig("msg1", ts, body)
    main._EVENTSUB_SECRET = _SECRET
    assert main._verify_eventsub_signature("msg1", ts, sig, tampered) is False


# ---------------------------------------------------------------------------
# _handle_eventsub_event — reward-match filtering
# ---------------------------------------------------------------------------
async def test_handle_event_ignored_when_no_user():
    payload = {
        "event": {
            "broadcaster_user_id": "b_unknown",
            "user_id": "v1", "user_name": "viewer1",
            "id": "rid1", "reward": {"id": "rw1"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=None)):
        with patch("main.db.add_redemption", AsyncMock()) as mock_add:
            await main._handle_eventsub_event(payload)
    mock_add.assert_not_called()


async def test_handle_event_ignored_when_wrong_reward():
    user = {
        "session_id": "s1", "twitch_user_id": "b1",
        "twitch_auth_token": "tok", "twitch_redeem_id": "correct-reward",
    }
    payload = {
        "event": {
            "broadcaster_user_id": "b1",
            "user_id": "v1", "user_name": "viewer1",
            "id": "rid1", "reward": {"id": "wrong-reward"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=user)):
        with patch("main.db.add_redemption", AsyncMock()) as mock_add:
            await main._handle_eventsub_event(payload)
    mock_add.assert_not_called()


async def test_handle_event_adds_redemption_and_sends_chat():
    user = {
        "session_id": "s1", "twitch_user_id": "b1",
        "twitch_auth_token": "tok", "twitch_redeem_id": "rw1",
    }
    payload = {
        "event": {
            "broadcaster_user_id": "b1",
            "user_id": "v1", "user_name": "viewer1",
            "id": "rid1", "reward": {"id": "rw1"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=user)), \
         patch("main.db.has_pending_redemption", AsyncMock(return_value=False)), \
         patch("main.db.has_recent_invite", AsyncMock(return_value=False)), \
         patch("main.db.add_redemption", AsyncMock()) as mock_add, \
         patch("main.twitch.send_chat_message", AsyncMock(return_value=True)) as mock_chat, \
         patch("main.ext_pubsub.send_whisper", AsyncMock()):
        await main._handle_eventsub_event(payload)

    mock_add.assert_called_once_with("s1", "v1", "viewer1", "rid1", "rw1")
    mock_chat.assert_called_once()
    assert "@viewer1" in mock_chat.call_args[0][2]


async def test_handle_event_duplicate_cancels_redemption():
    user = {
        "session_id": "s1", "twitch_user_id": "b1",
        "twitch_auth_token": "tok", "twitch_redeem_id": "rw1",
    }
    payload = {
        "event": {
            "broadcaster_user_id": "b1",
            "user_id": "v1", "user_name": "viewer1",
            "id": "rid1", "reward": {"id": "rw1"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=user)):
        with patch("main.db.has_pending_redemption", AsyncMock(return_value=True)):
            with patch("main.twitch.cancel_redemption", AsyncMock(return_value=True)) as mock_cancel:
                with patch("main.db.add_redemption", AsyncMock()) as mock_add:
                    await main._handle_eventsub_event(payload)

    mock_cancel.assert_called_once_with("b1", "rw1", "rid1", "tok")
    mock_add.assert_not_called()


async def test_handle_event_recent_invite_cancels_redemption():
    """If viewer has a recent invite (from follow-age or prior claim), cancel."""
    user = {
        "session_id": "s1", "twitch_user_id": "b1",
        "twitch_auth_token": "tok", "twitch_redeem_id": "rw1",
        "ext_cooldown_days": 30,
    }
    payload = {
        "event": {
            "broadcaster_user_id": "b1",
            "user_id": "v1", "user_name": "viewer1",
            "id": "rid1", "reward": {"id": "rw1"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=user)), \
         patch("main.db.has_pending_redemption", AsyncMock(return_value=False)), \
         patch("main.db.has_recent_invite", AsyncMock(return_value=True)), \
         patch("main.twitch.cancel_redemption", AsyncMock(return_value=True)) as mock_cancel, \
         patch("main.twitch.send_chat_message", AsyncMock(return_value=True)) as mock_chat, \
         patch("main.db.add_redemption", AsyncMock()) as mock_add:
        await main._handle_eventsub_event(payload)

    mock_cancel.assert_called_once_with("b1", "rw1", "rid1", "tok")
    mock_add.assert_not_called()
    mock_chat.assert_called_once()
    assert "recent invite" in mock_chat.call_args[0][2]


# ---------------------------------------------------------------------------
# _handle_eventsub_revocation
# ---------------------------------------------------------------------------
async def test_handle_revocation_calls_bot_handle_revocation():
    user = {"session_id": "s1", "twitch_user_id": "b1"}
    payload = {
        "subscription": {
            "id": "sub123",
            "status": "authorization_revoked",
            "condition": {"broadcaster_user_id": "b1"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=user)):
        with patch("main.bot.handle_revocation", AsyncMock()) as mock_rev:
            await main._handle_eventsub_revocation(payload)
    mock_rev.assert_called_once_with("s1")


async def test_handle_revocation_no_user_is_silent():
    payload = {
        "subscription": {
            "id": "sub123",
            "status": "authorization_revoked",
            "condition": {"broadcaster_user_id": "unknown"},
        }
    }
    with patch("main.db.get_user_by_twitch_id", AsyncMock(return_value=None)):
        with patch("main.bot.handle_revocation", AsyncMock()) as mock_rev:
            await main._handle_eventsub_revocation(payload)
    mock_rev.assert_not_called()
