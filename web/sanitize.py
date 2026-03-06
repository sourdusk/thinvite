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

"""
Input validation helpers.

All functions return True only when the value is safe to use and in the
expected format.  Callers should treat a False return as a reason to reject
the input without ever sending it to the database or a third-party API.
"""
import re

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Twitch usernames: 1–25 chars, alphanumeric + underscores.
# (Twitch's official minimum is 4, but some legacy accounts are shorter;
# we keep 1 as the minimum so the API layer can decide.)
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,25}$")

# Standard RFC-4122 UUID (used for Twitch channel-point reward IDs, etc.)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Discord / Twitch snowflake IDs: 17–20 decimal digits.
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")

# RFC-5321 practical email address (max 254 chars per spec).
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


# ---------------------------------------------------------------------------
# Public validators
# ---------------------------------------------------------------------------

def is_valid_twitch_username(value: str) -> bool:
    """Return True if *value* looks like a valid Twitch username."""
    return bool(value and _USERNAME_RE.match(value))


def is_valid_uuid(value: str) -> bool:
    """Return True if *value* is a well-formed UUID (any version)."""
    return bool(value and _UUID_RE.match(value))


def is_valid_snowflake(value) -> bool:
    """Return True if *value* is a valid Discord/Twitch snowflake ID.

    Accepts strings or integers; rejects None, empty strings, and values
    that contain non-digit characters or fall outside the 17–20 digit range.
    """
    if not value:
        return False
    return bool(_SNOWFLAKE_RE.match(str(value)))


def is_positive_int(value) -> bool:
    """Return True if *value* can be coerced to a positive integer."""
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def is_valid_email(value: str) -> bool:
    """Return True if *value* looks like a valid email address (≤ 254 chars)."""
    return bool(value and len(value) <= 254 and _EMAIL_RE.match(value))
