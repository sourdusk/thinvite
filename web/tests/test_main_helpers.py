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

"""Tests for the pure helper functions in main.py.

main.py calls ui.run() at module level (starts a server) and
app.add_static_files() (checks directory existence) — both must be suppressed
before 'main' is imported.  Python caches the module in sys.modules after the
first import, so these side-effects happen exactly once, right here.
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import nicegui first so we can patch specific attributes on its objects.
from nicegui import app as _nicegui_app, ui as _nicegui_ui

# ---------------------------------------------------------------------------
# Suppress ui.run() and app.add_static_files() before importing main.
# ---------------------------------------------------------------------------
_ui_run_patcher = patch.object(_nicegui_ui, "run")
_ui_run_patcher.start()

_static_patcher = patch.object(_nicegui_app, "add_static_files")
_static_patcher.start()

import main  # noqa: E402 — intentionally after the patches


# ---------------------------------------------------------------------------
# Fixture: replace NiceGUI's request-scoped user storage with a plain dict
# so that _is_form_on_cooldown can be tested without a live NiceGUI client.
# ---------------------------------------------------------------------------
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def user_storage(monkeypatch):
    """Install a plain dict as app.storage.user for the duration of one test."""
    data: dict = {}
    fake_storage = MagicMock()
    fake_storage.user = data
    monkeypatch.setattr(main, "app", MagicMock(storage=fake_storage))
    return data


# ---------------------------------------------------------------------------
# _is_beta_user
# ---------------------------------------------------------------------------
class TestIsBetaUser:
    def test_missing_file_allows_everyone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "_BETA_USERS_FILE", tmp_path / "no_such_file.txt")
        assert main._is_beta_user("anyone") is True

    def test_empty_file_allows_everyone(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        assert main._is_beta_user("anyone") is True

    def test_only_comments_allows_everyone(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("# This is a comment\n# Another comment\n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        assert main._is_beta_user("anyone") is True

    def test_user_in_list_is_allowed(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("sourk9\nstreamer2\n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        assert main._is_beta_user("sourk9") is True

    def test_user_not_in_list_is_rejected(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("sourk9\nstreamer2\n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        assert main._is_beta_user("unlisted_user") is False

    def test_check_is_case_insensitive(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("sourk9\n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        assert main._is_beta_user("SOURK9") is True
        assert main._is_beta_user("SourK9") is True

    def test_comments_are_ignored(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("# sourk9 is listed here\nsourk9\n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        # The comment line must not count as a username.
        assert main._is_beta_user("# sourk9 is listed here") is False
        assert main._is_beta_user("sourk9") is True

    def test_blank_lines_are_ignored(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("\n\nsourk9\n\n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        # Blank lines must not count as the empty username "".
        assert main._is_beta_user("") is False
        assert main._is_beta_user("sourk9") is True

    def test_whitespace_around_username_is_trimmed(self, tmp_path, monkeypatch):
        beta_file = tmp_path / "beta_users.txt"
        beta_file.write_text("  sourk9  \n")
        monkeypatch.setattr(main, "_BETA_USERS_FILE", beta_file)
        assert main._is_beta_user("sourk9") is True


# ---------------------------------------------------------------------------
# _is_form_on_cooldown
# ---------------------------------------------------------------------------
class TestIsFormOnCooldown:
    def test_first_call_is_not_on_cooldown(self, user_storage):
        result = main._is_form_on_cooldown("contact")
        assert result is False

    def test_set_cooldown_stamps_timestamp(self, user_storage):
        before = time.time()
        main._set_form_cooldown("contact")
        after = time.time()
        stamped = user_storage["_form_ts_contact"]
        assert before <= stamped <= after

    def test_check_after_set_is_on_cooldown(self, user_storage):
        main._set_form_cooldown("contact")
        result = main._is_form_on_cooldown("contact")
        assert result is True

    def test_call_after_window_is_not_on_cooldown(self, user_storage, monkeypatch):
        # Simulate a timestamp that is older than the cooldown window.
        expired = time.time() - main._FORM_COOLDOWN_SECONDS - 1
        user_storage["_form_ts_contact"] = expired
        result = main._is_form_on_cooldown("contact")
        assert result is False

    def test_different_keys_are_independent(self, user_storage):
        main._set_form_cooldown("contact")
        # "waitlist" has no timestamp yet — must not be on cooldown.
        result = main._is_form_on_cooldown("waitlist")
        assert result is False

    def test_same_key_set_twice_second_check_is_throttled(self, user_storage):
        main._set_form_cooldown("waitlist")
        assert main._is_form_on_cooldown("waitlist") is True


# ---------------------------------------------------------------------------
# CORS middleware for /api/ext/* routes
# ---------------------------------------------------------------------------
class TestCorsMiddleware:
    @pytest.fixture
    def middleware(self):
        async def dummy_app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b"{}"})

        return main._SecurityHeadersMiddleware(dummy_app)

    @pytest.mark.asyncio
    async def test_options_preflight_returns_204(self, middleware):
        scope = {
            "type": "http", "method": "OPTIONS", "path": "/api/ext/status",
            "headers": [(b"origin", b"https://extension-files.twitch.tv")],
        }
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        await middleware(scope, mock_receive, mock_send)

        assert responses[0]["status"] == 204
        hdrs = dict(responses[0]["headers"])
        assert hdrs[b"access-control-allow-origin"] == b"https://extension-files.twitch.tv"
        assert b"Authorization" in hdrs[b"access-control-allow-headers"]

    @pytest.mark.asyncio
    async def test_options_preflight_localhost(self, middleware):
        scope = {
            "type": "http", "method": "OPTIONS", "path": "/api/ext/status",
            "headers": [(b"origin", b"https://localhost:8080")],
        }
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        await middleware(scope, mock_receive, mock_send)

        assert responses[0]["status"] == 204
        hdrs = dict(responses[0]["headers"])
        assert hdrs[b"access-control-allow-origin"] == b"https://localhost:8080"

    @pytest.mark.asyncio
    async def test_get_ext_route_has_cors_headers(self, middleware):
        scope = {
            "type": "http", "method": "GET", "path": "/api/ext/status",
            "headers": [(b"origin", b"https://extension-files.twitch.tv")],
        }
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        await middleware(scope, mock_receive, mock_send)

        start = responses[0]
        from starlette.datastructures import MutableHeaders
        hdrs = MutableHeaders(scope=start)
        assert hdrs["access-control-allow-origin"] == "https://extension-files.twitch.tv"

    @pytest.mark.asyncio
    async def test_non_ext_route_has_no_cors(self, middleware):
        scope = {
            "type": "http", "method": "GET", "path": "/streamer",
            "headers": [(b"origin", b"https://extension-files.twitch.tv")],
        }
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        await middleware(scope, mock_receive, mock_send)

        start = responses[0]
        from starlette.datastructures import MutableHeaders
        hdrs = MutableHeaders(scope=start)
        assert hdrs.get("access-control-allow-origin") is None

    @pytest.mark.asyncio
    async def test_unknown_origin_gets_no_cors(self, middleware):
        scope = {
            "type": "http", "method": "GET", "path": "/api/ext/status",
            "headers": [(b"origin", b"https://evil.com")],
        }
        responses = []

        async def mock_send(msg):
            responses.append(msg)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        await middleware(scope, mock_receive, mock_send)

        start = responses[0]
        from starlette.datastructures import MutableHeaders
        hdrs = MutableHeaders(scope=start)
        assert hdrs.get("access-control-allow-origin") is None
