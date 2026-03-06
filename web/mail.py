"""
Mailjet helpers for Thinvite.

Sending (v3.1 API)   — contact form and owner notifications.
Contact lists (v3 REST API) — waitlist signups added to the "Thinvite" list.

Required environment variables (in web/.env):
    MAILJET_API_KEY        Mailjet API key
    MAILJET_SECRET_KEY     Mailjet Secret key
    MAILJET_SENDER_EMAIL   A sender address verified in your Mailjet account
                           (e.g. noreply@thinvite.sourk9.com or dusk@sourk9.com)
"""
import logging
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger()

_SEND_URL = "https://api.mailjet.com/v3.1/send"
_REST_URL = "https://api.mailjet.com/v3/REST"
_TIMEOUT  = aiohttp.ClientTimeout(total=10)

_OWNER_EMAIL = "dusk@sourk9.com"
_OWNER_NAME  = "SourK9"

# In-process cache: contact list name → Mailjet list ID
_list_id_cache: dict = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(
        os.getenv("MAILJET_API_KEY", ""),
        os.getenv("MAILJET_SECRET_KEY", ""),
    )


def _sender_email() -> str:
    return os.getenv("MAILJET_SENDER_EMAIL", "noreply@thinvite.sourk9.com")


async def _send(payload: dict) -> bool:
    """POST a v3.1 send payload; returns True on success."""
    if not os.getenv("MAILJET_API_KEY") or not os.getenv("MAILJET_SECRET_KEY"):
        logger.error("Mailjet credentials not configured — skipping send")
        return False
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(_SEND_URL, json=payload, auth=_auth()) as resp:
            if resp.status != 200:
                logger.error(
                    "Mailjet send failed: %s %s", resp.status, await resp.text()
                )
                return False
            return True


async def _get_list_id(list_name: str) -> int | None:
    """
    Look up a Mailjet contact list ID by name.
    Result is cached in _list_id_cache for the lifetime of the process.
    """
    if list_name in _list_id_cache:
        return _list_id_cache[list_name]
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(
            f"{_REST_URL}/contactslist",
            params={"Name": list_name},
            auth=_auth(),
        ) as resp:
            if resp.status != 200:
                logger.error(
                    "Mailjet contactslist lookup failed: %s", resp.status
                )
                return None
            data = await resp.json()
            if data.get("Count", 0) < 1:
                logger.error("Mailjet contact list '%s' not found", list_name)
                return None
            list_id = data["Data"][0]["ID"]
            _list_id_cache[list_name] = list_id
            return list_id


async def _add_contact_to_list(email: str, list_name: str) -> bool:
    """
    Add *email* to the named Mailjet contact list (addnoforce — no duplicate
    sends to existing subscribers).
    """
    list_id = await _get_list_id(list_name)
    if list_id is None:
        return False
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.post(
            f"{_REST_URL}/contactslist/{list_id}/managemanycontacts",
            json={"Action": "addnoforce", "Contacts": [{"Email": email}]},
            auth=_auth(),
        ) as resp:
            if resp.status not in (200, 201):
                logger.error(
                    "Mailjet add-contact failed: %s %s",
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
    Sets Reply-To so the owner can reply directly to the sender.
    """
    payload = {
        "Messages": [
            {
                "From":    {"Email": _sender_email(), "Name": "Thinvite"},
                "To":      [{"Email": _OWNER_EMAIL, "Name": _OWNER_NAME}],
                "ReplyTo": {"Email": from_email, "Name": from_name},
                "Subject": f"[Thinvite Contact] Message from {from_name}",
                "TextPart": (
                    f"Name:  {from_name}\n"
                    f"Email: {from_email}\n\n"
                    f"{message}"
                ),
            }
        ]
    }
    return await _send(payload)


async def add_to_waitlist(email: str, twitch_username: str = "") -> bool:
    """
    Add *email* to the "Thinvite" Mailjet contact list and send the owner a
    notification.  Returns True only when both operations succeed.
    """
    added = await _add_contact_to_list(email, "Thinvite")

    body = f"New beta waitlist signup\n\nEmail: {email}"
    if twitch_username:
        body += f"\nTwitch: {twitch_username}"

    payload = {
        "Messages": [
            {
                "From":    {"Email": _sender_email(), "Name": "Thinvite"},
                "To":      [{"Email": _OWNER_EMAIL, "Name": _OWNER_NAME}],
                "Subject": "[Thinvite] New Waitlist Signup",
                "TextPart": body,
            }
        ]
    }
    notified = await _send(payload)
    return added and notified
