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
Brevo (formerly Sendinblue) helpers for Thinvite.

Transactional email (v3 SMTP API) — contact form and owner notifications.
Contact lists (v3 Contacts API)   — waitlist signups added to the "Thinvite" list.

Required environment variables (in web/.env):
    BREVO_API_KEY       Brevo API key
    SENDER_ADDRESS      A sender address verified in your Brevo account
    OWNER_EMAIL         Receives contact form and waitlist notifications
    OWNER_NAME          Display name for the owner recipient
"""
import logging
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger()

_SEND_URL = "https://api.brevo.com/v3/smtp/email"
_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
_LISTS_URL = "https://api.brevo.com/v3/contacts/lists"
_TIMEOUT = aiohttp.ClientTimeout(total=10)

# In-process cache: contact list name → Brevo list ID
_list_id_cache: dict = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "api-key": os.getenv("BREVO_API_KEY", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _sender_email() -> str:
    return os.getenv("SENDER_ADDRESS", "")


async def _send(payload: dict) -> bool:
    """POST a transactional email payload; returns True on success."""
    if not os.getenv("BREVO_API_KEY"):
        logger.error("Brevo API key not configured — skipping send")
        return False
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            _SEND_URL, json=payload, headers=_headers()
        ) as resp:
            if resp.status not in (200, 201):
                logger.error(
                    "Brevo send failed: %s %s", resp.status, await resp.text()
                )
                return False
            return True


async def _get_list_id(list_name: str) -> int | None:
    """
    Look up a Brevo contact list ID by name.
    Result is cached in _list_id_cache for the lifetime of the process.
    """
    if list_name in _list_id_cache:
        return _list_id_cache[list_name]
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(
            _LISTS_URL,
            headers=_headers(),
        ) as resp:
            if resp.status != 200:
                logger.error("Brevo list lookup failed: %s", resp.status)
                return None
            data = await resp.json()
            for lst in data.get("lists", []):
                if lst.get("name") == list_name:
                    _list_id_cache[list_name] = lst["id"]
                    return lst["id"]
            logger.error("Brevo contact list '%s' not found", list_name)
            return None


async def _add_contact_to_list(email: str, list_name: str) -> bool:
    """
    Add *email* to the named Brevo contact list.
    Creates or updates the contact with the list assignment in one call.
    """
    list_id = await _get_list_id(list_name)
    if list_id is None:
        return False
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        # Create or update the contact (updateEnabled avoids duplicates).
        async with session.post(
            _CONTACTS_URL,
            json={"email": email, "listIds": [list_id], "updateEnabled": True},
            headers=_headers(),
        ) as resp:
            if resp.status not in (200, 201, 204):
                logger.error(
                    "Brevo add-contact failed: %s %s",
                    resp.status,
                    await resp.text(),
                )
                return False
            return True


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def send_contact_email(
    from_name: str, from_email: str, message: str
) -> bool:
    """
    Forward a contact form submission to the site owner.
    Sets replyTo so the owner can reply directly to the sender.
    """
    payload = {
        "sender": {"email": _sender_email(), "name": "Thinvite"},
        "to": [{"email": os.getenv("OWNER_EMAIL", ""), "name": os.getenv("OWNER_NAME", "")}],
        "replyTo": {"email": from_email, "name": from_name},
        "subject": f"[Thinvite Contact] Message from {from_name}",
        "textContent": (
            f"Name:  {from_name}\n"
            f"Email: {from_email}\n\n"
            f"{message}"
        ),
    }
    return await _send(payload)


async def add_to_waitlist(email: str, twitch_username: str = "") -> bool:
    """
    Add *email* to the "Thinvite" Brevo contact list and send the owner a
    notification.  Returns True only when both operations succeed.
    """
    added = await _add_contact_to_list(email, "Thinvite")

    body = f"New beta waitlist signup\n\nEmail: {email}"
    if twitch_username:
        body += f"\nTwitch: {twitch_username}"

    payload = {
        "sender": {"email": _sender_email(), "name": "Thinvite"},
        "to": [{"email": os.getenv("OWNER_EMAIL", ""), "name": os.getenv("OWNER_NAME", "")}],
        "subject": "[Thinvite] New Waitlist Signup",
        "textContent": body,
    }
    notified = await _send(payload)
    return added and notified
