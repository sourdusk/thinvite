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

"""Tests for mail.py — Brevo send and contact-list helpers."""
import pytest
from unittest.mock import patch

import mail
from tests.conftest import make_aiohttp_response, make_aiohttp_session


@pytest.fixture(autouse=True)
def clear_list_cache():
    """Start every test with an empty list-ID cache to prevent cross-test pollution."""
    mail._list_id_cache.clear()
    yield
    mail._list_id_cache.clear()


# ---------------------------------------------------------------------------
# send_contact_email  (uses _send internally)
# ---------------------------------------------------------------------------
async def test_send_contact_email_success():
    resp = make_aiohttp_response({}, status=201)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await mail.send_contact_email("Alice", "alice@example.com", "Hi!")
    assert result is True


async def test_send_contact_email_api_failure_returns_false():
    resp = make_aiohttp_response({}, status=400)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await mail.send_contact_email("Alice", "alice@example.com", "Hi!")
    assert result is False


async def test_send_contact_email_missing_api_key_returns_false(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    result = await mail.send_contact_email("Alice", "alice@example.com", "Hi!")
    assert result is False


async def test_send_contact_email_payload_to_is_owner():
    """Email must be addressed to the site owner, not the sender."""
    resp = make_aiohttp_response({}, status=201)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        await mail.send_contact_email("Alice", "alice@example.com", "My message")
    payload = sess.post.call_args[1]["json"]
    assert payload["to"][0]["email"] == "owner@test.thinvite.com"


async def test_send_contact_email_reply_to_is_sender():
    """replyTo must be set to the sender so the owner can reply directly."""
    resp = make_aiohttp_response({}, status=201)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        await mail.send_contact_email("Alice", "alice@example.com", "My message")
    payload = sess.post.call_args[1]["json"]
    assert payload["replyTo"]["email"] == "alice@example.com"
    assert payload["replyTo"]["name"] == "Alice"


async def test_send_contact_email_body_contains_message_and_name():
    resp = make_aiohttp_response({}, status=201)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        await mail.send_contact_email("Bob", "bob@example.com", "Hello world")
    payload = sess.post.call_args[1]["json"]
    assert "Hello world" in payload["textContent"]
    assert "Bob" in payload["subject"]


# ---------------------------------------------------------------------------
# _get_list_id
# ---------------------------------------------------------------------------
async def test_get_list_id_success():
    data = {"lists": [{"id": 42, "name": "Thinvite"}]}
    resp = make_aiohttp_response(data, status=200)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        list_id = await mail._get_list_id("Thinvite")
    assert list_id == 42


async def test_get_list_id_caches_result():
    """A second call must return the cached value without making another HTTP request."""
    data = {"lists": [{"id": 99, "name": "Thinvite"}]}
    resp = make_aiohttp_response(data, status=200)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        first = await mail._get_list_id("Thinvite")
    # Second call is outside the patch; the cache must be hit, no real HTTP call.
    second = await mail._get_list_id("Thinvite")
    assert first == second == 99
    sess.get.assert_called_once()


async def test_get_list_id_not_found_returns_none():
    data = {"lists": []}
    resp = make_aiohttp_response(data, status=200)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await mail._get_list_id("NonExistent")
    assert result is None


async def test_get_list_id_api_error_returns_none():
    resp = make_aiohttp_response({}, status=500)
    sess = make_aiohttp_session(get_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await mail._get_list_id("Thinvite")
    assert result is None


# ---------------------------------------------------------------------------
# _add_contact_to_list
# ---------------------------------------------------------------------------
async def test_add_contact_to_list_success():
    # Session 1 (GET): look up list ID. Session 2 (POST): create contact.
    list_resp = make_aiohttp_response(
        {"lists": [{"id": 7, "name": "Thinvite"}]}, status=200
    )
    add_resp = make_aiohttp_response({}, status=201)
    sessions = iter([
        make_aiohttp_session(get_resp=list_resp),
        make_aiohttp_session(post_resp=add_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        result = await mail._add_contact_to_list("user@example.com", "Thinvite")
    assert result is True


async def test_add_contact_to_list_list_not_found_returns_false():
    list_resp = make_aiohttp_response({"lists": []}, status=200)
    sess = make_aiohttp_session(get_resp=list_resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await mail._add_contact_to_list("user@example.com", "NoSuchList")
    assert result is False


async def test_add_contact_to_list_api_error_returns_false():
    # Pre-populate the cache so no GET is needed.
    mail._list_id_cache["Thinvite"] = 7
    add_resp = make_aiohttp_response({}, status=400)
    sess = make_aiohttp_session(post_resp=add_resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await mail._add_contact_to_list("user@example.com", "Thinvite")
    assert result is False


async def test_add_contact_to_list_sends_update_enabled():
    """updateEnabled must be True to avoid duplicates."""
    mail._list_id_cache["Thinvite"] = 5
    add_resp = make_aiohttp_response({}, status=201)
    sess = make_aiohttp_session(post_resp=add_resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        await mail._add_contact_to_list("user@example.com", "Thinvite")
    payload = sess.post.call_args[1]["json"]
    assert payload["updateEnabled"] is True
    assert payload["email"] == "user@example.com"
    assert 5 in payload["listIds"]


# ---------------------------------------------------------------------------
# add_to_waitlist
# ---------------------------------------------------------------------------
async def test_add_to_waitlist_success():
    list_resp = make_aiohttp_response(
        {"lists": [{"id": 3, "name": "Thinvite"}]}, status=200
    )
    add_resp = make_aiohttp_response({}, status=201)
    notify_resp = make_aiohttp_response({}, status=201)
    sessions = iter([
        make_aiohttp_session(get_resp=list_resp),
        make_aiohttp_session(post_resp=add_resp),
        make_aiohttp_session(post_resp=notify_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        result = await mail.add_to_waitlist("user@example.com", "streamer1")
    assert result is True


async def test_add_to_waitlist_list_add_fails_returns_false():
    """If the contact list is not found the overall call returns False."""
    list_resp = make_aiohttp_response({"lists": []}, status=200)
    notify_resp = make_aiohttp_response({}, status=201)
    sessions = iter([
        make_aiohttp_session(get_resp=list_resp),
        make_aiohttp_session(post_resp=notify_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        result = await mail.add_to_waitlist("user@example.com")
    assert result is False


async def test_add_to_waitlist_notification_fails_returns_false():
    """If the owner notification fails the overall call returns False."""
    list_resp = make_aiohttp_response(
        {"lists": [{"id": 3, "name": "Thinvite"}]}, status=200
    )
    add_resp = make_aiohttp_response({}, status=201)
    notify_resp = make_aiohttp_response({}, status=500)
    sessions = iter([
        make_aiohttp_session(get_resp=list_resp),
        make_aiohttp_session(post_resp=add_resp),
        make_aiohttp_session(post_resp=notify_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        result = await mail.add_to_waitlist("user@example.com")
    assert result is False


async def test_add_to_waitlist_notification_includes_twitch_username():
    """Twitch username must appear in the owner notification body."""
    list_resp = make_aiohttp_response(
        {"lists": [{"id": 3, "name": "Thinvite"}]}, status=200
    )
    add_resp = make_aiohttp_response({}, status=201)
    notify_resp = make_aiohttp_response({}, status=201)
    notify_sess = make_aiohttp_session(post_resp=notify_resp)
    sessions = iter([
        make_aiohttp_session(get_resp=list_resp),
        make_aiohttp_session(post_resp=add_resp),
        notify_sess,
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        await mail.add_to_waitlist("user@example.com", "coolstreamer")
    payload = notify_sess.post.call_args[1]["json"]
    body = payload["textContent"]
    assert "coolstreamer" in body
    assert "user@example.com" in body


async def test_add_to_waitlist_without_twitch_username_succeeds():
    """Twitch username is optional; omitting it must not break the call."""
    list_resp = make_aiohttp_response(
        {"lists": [{"id": 3, "name": "Thinvite"}]}, status=200
    )
    add_resp = make_aiohttp_response({}, status=201)
    notify_resp = make_aiohttp_response({}, status=201)
    sessions = iter([
        make_aiohttp_session(get_resp=list_resp),
        make_aiohttp_session(post_resp=add_resp),
        make_aiohttp_session(post_resp=notify_resp),
    ])
    with patch("aiohttp.ClientSession", side_effect=lambda **kw: next(sessions)):
        result = await mail.add_to_waitlist("user@example.com")
    assert result is True
