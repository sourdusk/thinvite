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

    def test_first_call_stamps_timestamp(self, user_storage):
        before = time.time()
        main._is_form_on_cooldown("contact")
        after = time.time()
        stamped = user_storage["_form_ts_contact"]
        assert before <= stamped <= after

    def test_second_call_within_window_is_on_cooldown(self, user_storage):
        main._is_form_on_cooldown("contact")   # first call — stamps timestamp
        result = main._is_form_on_cooldown("contact")  # second call — on cooldown
        assert result is True

    def test_call_after_window_is_not_on_cooldown(self, user_storage, monkeypatch):
        # Simulate a timestamp that is older than the cooldown window.
        expired = time.time() - main._FORM_COOLDOWN_SECONDS - 1
        user_storage["_form_ts_contact"] = expired
        result = main._is_form_on_cooldown("contact")
        assert result is False

    def test_different_keys_are_independent(self, user_storage):
        main._is_form_on_cooldown("contact")   # stamps "contact"
        # "waitlist" has no timestamp yet — must not be on cooldown.
        result = main._is_form_on_cooldown("waitlist")
        assert result is False

    def test_same_key_called_twice_second_is_throttled(self, user_storage):
        main._is_form_on_cooldown("waitlist")
        assert main._is_form_on_cooldown("waitlist") is True
