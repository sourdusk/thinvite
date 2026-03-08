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

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import uuid
import logging
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from nicegui import app, ui
from fastapi import Request
from fastapi import Response
from fastapi.responses import PlainTextResponse, JSONResponse
from starlette.datastructures import MutableHeaders


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

import bot
import captcha
import discorddb
import expiry
import mail
import twitch
import db
import sanitize

logger = logging.getLogger()

_SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

# EventSub webhook secret — must be non-empty; asserted in startup().
_EVENTSUB_SECRET = os.getenv("THINVITE_EVENTSUB_SECRET", "")
# Public site URL — used for OAuth redirects, EventSub callback, CSP, and meta tags.
_SITE_URL = os.getenv("SITE_URL", "")
# wss:// equivalent for the CSP connect-src directive.
_SITE_WSS = _SITE_URL.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


# ---------------------------------------------------------------------------
# Security headers — applied to every response
# ---------------------------------------------------------------------------
class _SecurityHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "SAMEORIGIN"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                    "https://challenges.cloudflare.com; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "img-src 'self' data: https:; "
                    "font-src 'self' data: https://fonts.gstatic.com; "
                    f"connect-src 'self' {_SITE_WSS}; "
                    "frame-src https://challenges.cloudflare.com; "
                    "object-src 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self';"
                )
                # Don't prevent caching of versioned static assets. NiceGUI's
                # JS files (vue, quasar, etc.) must load from cache so Firefox
                # serves them in order; parallel HTTP/2 fetches arrive out of
                # order causing "window.Vue is undefined" in quasar.umd.prod.js.
                path = scope.get("path", "")
                is_static = (
                    ("/_nicegui/" in path and "/static/" in path)
                    or path.startswith("/static/")
                )
                if not is_static:
                    headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Session cookie security: Secure flag, 30-day Max-Age, server-side TTL
# ---------------------------------------------------------------------------
class _SessionSecurityMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Server-side 30-day TTL via raw Starlette session dict
        session = scope.get("session")
        if session is not None:
            issued = session.get("_thinvite_issued")
            now = time.time()
            if issued is None:
                session["_thinvite_issued"] = now
            elif now - issued > _SESSION_MAX_AGE_SECONDS:
                session.clear()
                session["_thinvite_issued"] = now

        async def send_with_session_headers(message):
            if message["type"] == "http.response.start":
                # Patch Set-Cookie headers: add Secure + Max-Age to session cookies
                raw = list(message.get("headers", []))
                new_raw = []
                for name, value in raw:
                    if name.lower() == b"set-cookie":
                        val = value.decode("latin-1")
                        cookie_name = val.split("=")[0].strip()
                        if cookie_name in ("session", "id"):
                            if "secure" not in val.lower():
                                val += "; Secure"
                            if "max-age" not in val.lower():
                                val += f"; Max-Age={_SESSION_MAX_AGE_SECONDS}"
                            value = val.encode("latin-1")
                    new_raw.append((name, value))
                message = {**message, "headers": new_raw}
            await send(message)

        await self.app(scope, receive, send_with_session_headers)


app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_SessionSecurityMiddleware)


# ---------------------------------------------------------------------------
# In-memory rate limiter — shared across all API callback endpoints
# ---------------------------------------------------------------------------
_api_hits: dict = defaultdict(list)
_api_hits_last_sweep: float = 0.0
_RATE_WINDOW = 60   # seconds
_RATE_MAX = 10      # max attempts per window per IP


def _is_rate_limited(request: Request) -> bool:
    # request.client.host is already the real client IP: uvicorn resolves
    # X-Forwarded-For automatically because forwarded_allow_ips="127.0.0.1"
    # is set in ui.run(), so reading the header directly is unnecessary and
    # would allow spoofing if the proxy configuration ever changes.
    ip = request.client.host
    now = time.monotonic()

    # Periodically sweep stale entries so the dict doesn't grow unboundedly
    # under scanning traffic.  One sweep per rate window is sufficient.
    global _api_hits_last_sweep
    if now - _api_hits_last_sweep > _RATE_WINDOW:
        _api_hits_last_sweep = now
        stale = [k for k, v in _api_hits.items() if not v or v[-1] < now - _RATE_WINDOW]
        for k in stale:
            del _api_hits[k]

    hits = [t for t in _api_hits[ip] if now - t < _RATE_WINDOW]
    hits.append(now)
    _api_hits[ip] = hits
    return len(hits) > _RATE_MAX


# ---------------------------------------------------------------------------
# Application-level session helpers
# ---------------------------------------------------------------------------
def _sess_id() -> str:
    """Return the effective application session ID.

    After the first OAuth round-trip the session is rotated and the new token
    is stored in app.storage.user["_sess"].  Before rotation the NiceGUI
    browser ID is used as a fallback.
    """
    return app.storage.user.get("_sess") or app.storage.browser["id"]


async def _rotate_session_id() -> tuple:
    """Generate a fresh session token, migrate the DB row, return (old, new)."""
    old_id = _sess_id()
    new_id = str(uuid.uuid4())
    await db.rotate_session(old_id, new_id)
    app.storage.user["_sess"] = new_id
    return old_id, new_id


# ---------------------------------------------------------------------------
# Beta-tester allowlist
# ---------------------------------------------------------------------------
_BETA_USERS_FILE = pathlib.Path(__file__).parent / "beta_users.txt"


def _is_beta_user(username: str) -> bool:
    """Return True if *username* is permitted during the current beta phase.

    Re-reads the allowlist file on every call so changes take effect without
    a restart.  An empty file or a missing file disables beta restrictions
    (i.e. all users are allowed).
    """
    try:
        lines = _BETA_USERS_FILE.read_text().splitlines()
    except FileNotFoundError:
        return True
    beta_users = {
        l.strip().lower() for l in lines if l.strip() and not l.startswith("#")
    }
    return not beta_users or username.lower() in beta_users


# ---------------------------------------------------------------------------
# Per-session form submission rate-limiter
# ---------------------------------------------------------------------------
_FORM_COOLDOWN_SECONDS = 60


def _is_form_on_cooldown(key: str) -> bool:
    """Return True if a form identified by *key* was submitted recently."""
    store_key = f"_form_ts_{key}"
    last = app.storage.user.get(store_key, 0)
    return time.time() - last < _FORM_COOLDOWN_SECONDS


def _set_form_cooldown(key: str) -> None:
    """Record a successful submission so the cooldown window begins now."""
    app.storage.user[f"_form_ts_{key}"] = time.time()


# ---------------------------------------------------------------------------
# Shared UI chrome
# ---------------------------------------------------------------------------
def header(show_logout: bool = False):
    with ui.header(elevated=False).classes("items-center justify-between").style(
        "padding: 0 var(--space-6);"
    ):
        ui.html(
            '<a href="/" class="th-wordmark">'
            '<svg width="32" height="32" viewBox="0 0 56 56" fill="none">'
            "<defs>"
            '<linearGradient id="hdr-grad" x1="0" y1="0" x2="56" y2="56" gradientUnits="userSpaceOnUse">'
            '<stop offset="0%" stop-color="#7c5af0"/>'
            '<stop offset="100%" stop-color="#3d6ef5"/>'
            "</linearGradient>"
            "</defs>"
            '<rect width="56" height="56" rx="14" fill="url(#hdr-grad)"/>'
            '<circle cx="17.75" cy="36.25" r="10" stroke="white" stroke-width="2.5" fill="none"/>'
            '<circle cx="41.75" cy="16.25" r="6.5" fill="white"/>'
            '<line x1="28.25" y1="27.25" x2="35.75" y2="21.75" stroke="white" stroke-width="2.5" stroke-linecap="round"/>'
            "</svg>"
            '<span class="th-wordmark-text">Thin<span class="th-grad-text">vite</span></span>'
            "</a>",
            sanitize=False,
        )
        if show_logout:
            ui.button(
                "Logout",
                icon="logout",
                on_click=lambda: ui.navigate.to("/logout"),
            ).props("flat color=white size=sm").classes("ml-auto")


def footer():
    """Footer that also injects the cookie-notice banner when needed."""
    with ui.footer().style(
        "background: var(--color-surface); border-top: var(--border-default);"
    ):
        if not app.storage.user.get("cookie_consent"):
            with ui.row().classes(
                "w-full items-center justify-between q-px-md q-py-sm"
            ).style("border-bottom: var(--border-default);") as cookie_row:
                with ui.row().classes("items-center gap-sm"):
                    ui.label(
                        "This site uses a single strictly necessary session cookie to maintain your "
                        "authentication state. No tracking or advertising cookies are used."
                    ).classes("text-caption").style("color: var(--color-muted);")
                    ui.link("Privacy Policy", "/privacy").classes(
                        "text-caption text-primary"
                    )

                async def _accept():
                    app.storage.user["cookie_consent"] = True
                    cookie_row.delete()

                ui.button("Got it", on_click=_accept).props("dense size=sm color=primary")

        with ui.row().classes("w-full justify-between items-center q-px-md q-py-xs"):
            ui.label("\u00a9 SourK9 Designs, LLC 2026").classes("text-caption").style(
                "color: var(--color-muted);"
            )
            with ui.row().classes("gap-md items-center"):
                ui.link("Contact", "/contact").classes("text-caption").style(
                    "color: var(--color-muted);"
                )
                ui.link("Privacy Policy", "/privacy").classes("text-caption").style(
                    "color: var(--color-muted);"
                )
                ui.html(
                    '<a href="https://github.com/sourdusk/thinvite" target="_blank"'
                    ' rel="noopener" aria-label="GitHub" style="color: var(--color-muted);'
                    ' display: flex; align-items: center;">'
                    '<svg height="16" width="16" viewBox="0 0 16 16" fill="currentColor"'
                    ' aria-hidden="true">'
                    '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17'
                    ".55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94"
                    "-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87"
                    " 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59"
                    ".82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27"
                    " 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08"
                    " 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54"
                    ' 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16'
                    ' 8c0-4.42-3.58-8-8-8z"/>'
                    "</svg></a>",
                    sanitize=False,
                )


# ---------------------------------------------------------------------------
# Simple HTTP endpoints (not NiceGUI pages)
# ---------------------------------------------------------------------------
@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return PlainTextResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /logout\n"
    )


@app.get("/health", include_in_schema=False)
async def health_check():
    db_ok = False
    try:
        async with db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                db_ok = True
    except Exception:
        pass
    status = "ok" if db_ok else "degraded"
    return JSONResponse({"status": status, "db": db_ok}, status_code=200)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@ui.page("/", dark=True)
async def home_page():
    header()
    with ui.element("div").classes("th-hero"):
        ui.html(
            '<p class="th-label">Channel Redeems &middot; Discord Invites &middot; Verified Access</p>'
            '<h1 class="th-display">Twitch redeems &rarr; Discord access. <em>Securely.</em></h1>'
            '<p class="th-body">Streamers set up a channel point redeem. Viewers sign in with Twitch '
            "and receive a unique, single-use invite link. No sharing, no abuse.</p>",
            sanitize=False,
        )
        with ui.element("div").classes("th-cta-row"):
            ui.button("I\u2019m a streamer", on_click=lambda: ui.navigate.to("/streamer"), color=None).classes(
                "th-btn-primary"
            ).props("no-caps unelevated")
            ui.button("Redeem an invite", on_click=lambda: ui.navigate.to("/redeem"), color=None).classes(
                "th-btn-secondary"
            ).props("no-caps outline")
    footer()


@ui.page("/streamer", dark=True)
async def streamer_page():
    if "error" in app.storage.user and app.storage.user["error"] is not None:
        ui.notify(app.storage.user["error"])
        app.storage.user["error"] = None

    res = await db.ensure_db_user(_sess_id())
    if not res:
        app.storage.user["error"] = "Failed to create user"
        ui.navigate.to("/streamer")
        return

    # Fetch connection state before rendering — enables the beta gate and
    # avoids a second DB round-trip later in the function.
    twitch_user_exists, user_record = await asyncio.gather(
        twitch.user_exists(_sess_id()),
        db.get_user_by_session_id(_sess_id()),
    )
    discord_connected = (
        user_record is not None and user_record.get("discord_user_id") is not None
    )

    # Beta gate — redirect non-allowlisted Twitch users to the waitlist and
    # delete everything we stored about them so their data is not retained.
    if twitch_user_exists and user_record:
        twitch_username = user_record.get("twitch_user_name", "")
        if not _is_beta_user(twitch_username):
            await bot.unsubscribe(_sess_id())
            await db.delete_user_and_all_records(_sess_id())
            app.storage.user.clear()
            app.storage.user["waitlist_twitch"] = twitch_username
            ui.navigate.to("/waitlist")
            return

    async def twitch_login():
        state = secrets.token_hex(32)
        app.storage.user["state"] = state
        ui.navigate.to(twitch.generate_auth_code_link(state))

    header(show_logout=twitch_user_exists)
    if not (twitch_user_exists and discord_connected):
        with ui.row().classes("window-width row justify-center items-center"):
            ui.html(
                '<p style="font-size:3rem; font-weight:400; text-align:center;'
                " line-height:1.5; padding:0.5rem 1rem 1rem 1rem;"
                ' color:inherit; margin:0;">Begin by logging in to both Twitch and Discord.</p>',
                sanitize=False,
            )

    # Row with buttons
    with ui.row().classes("window-width row justify-center items-start").style(
        "gap: 15rem"
    ):
        # Column 1 (twitch)
        if twitch_user_exists:
            with ui.column().classes("items-center"):
                with ui.button(color="#6441a5", on_click=twitch_login).style(
                    "width: 10rem; height: 15rem;"
                ):
                    ui.image("/static/img/TwitchGlitchWhite.svg").props(
                        "fit=scale-down"
                    ).classes("m-auto").style("max-width: 10rem; max-height: 10rem;")
                    ui.label("Connected").classes("text-m m-auto")

                async def disconnect_twitch():
                    await bot.unsubscribe(_sess_id())
                    await db.disconnect_twitch(_sess_id())
                    ui.navigate.to("/streamer")

                ui.button(
                    "Disconnect Twitch", color="negative", on_click=disconnect_twitch
                ).props("flat size=sm").classes("q-mt-sm")

                redeems_raw = await twitch.get_channel_redeems(_sess_id())

                if redeems_raw is None:
                    ui.label(
                        "Could not load channel point redeems. Please refresh the page."
                    ).classes("text-body2 text-center text-negative")
                else:
                    redeems = {r["id"]: r["title"] for r in redeems_raw}
                    redeems_full = {r["id"]: r for r in redeems_raw}
                    current_redeem = await twitch.get_set_redeem(_sess_id())
                    if current_redeem is None and redeems:
                        current_redeem = (
                            next((rid for rid, title in redeems.items() if "discord" in title.lower()), None)
                            or next((rid for rid, title in redeems.items() if "server" in title.lower()), None)
                            or next(iter(redeems))
                        )
                        await twitch.update_twitch_redeem(_sess_id(), current_redeem)
                    ui.label("Select the channel point redeem").classes("text-body2 text-center")
                    ui.label("viewers must use to receive a Discord invite:").classes("text-body2 text-center")
                    sel = ui.select(redeems, value=current_redeem).classes("fit-width")

                    async def update_redeem():
                        new_id = sel.value
                        if new_id == current_redeem:
                            ui.notify("That redeem is already selected.", type="info")
                            return

                        reward = redeems_full.get(new_id, {})
                        skips_queue = reward.get("should_redemptions_skip_request_queue", False)

                        async def _apply_update():
                            # Always disable skip_request_queue so redemptions
                            # stay in the queue where Thinvite can manage them.
                            await twitch.update_reward_queue_setting(
                                _sess_id(), new_id, False
                            )
                            ok = await twitch.update_twitch_redeem(_sess_id(), new_id)
                            if ok:
                                ui.notify("Redeem updated!", type="positive")
                                ui.navigate.to("/streamer")
                            else:
                                ui.notify("Failed to update redeem.", type="negative")

                        if skips_queue:
                            with ui.dialog() as skip_dlg, ui.card().classes("q-pa-md"):
                                ui.label("Queue setting conflict").classes("text-h6")
                                ui.label(
                                    "This redeem currently skips the request queue. "
                                    "Thinvite needs 'Skip Request Queue' set to Off so "
                                    "it can manage redemptions. Proceed and change it?"
                                ).classes("q-mt-sm text-body2")
                                with ui.row().classes("justify-end q-mt-lg gap-sm"):
                                    ui.button("Cancel", on_click=skip_dlg.close).props("flat")

                                    async def _confirm_queue():
                                        skip_dlg.close()
                                        await _apply_update()

                                    ui.button("Proceed", on_click=_confirm_queue, color="primary")
                            skip_dlg.open()
                        else:
                            await _apply_update()

                    ui.button(color="#6441a5", text="Submit", on_click=update_redeem)
        else:
            with ui.column().classes("items-center"):
                with ui.button(color="#6441a5", on_click=twitch_login).style(
                    "width: 10rem; height: 15rem;"
                ):
                    ui.image("static/img/TwitchGlitchWhite.svg").props(
                        "fit=scale-down"
                    ).classes("m-auto").style("max-width: 10rem; max-height: 10rem;")
                    ui.label("Log in").classes("text-m m-auto")

        # Column 2 (discord)
        async def discord_login():
            client_id = os.getenv("THINVITE_DISCORD_ID")
            state = secrets.token_hex(32)
            app.storage.user["discord_state"] = state
            encoded_state = urllib.parse.quote(state)
            ui.navigate.to(
                f"https://discord.com/oauth2/authorize?client_id={client_id}"
                f"&permissions=1&response_type=code"
                f"&redirect_uri={urllib.parse.quote(_SITE_URL + '/api/discord')}"
                f"&integration_type=0&scope=identify+bot+guilds&state={encoded_state}"
            )

        with ui.column().classes("items-center"):
            with ui.button(color="#5865F2", on_click=discord_login).style(
                "width: 10rem; height: 15rem;"
            ) as but:
                if not twitch_user_exists:
                    but.disable()
                ui.image("static/img/discord-mark-white.svg").props(
                    "fit=scale-down"
                ).classes("m-auto").style("max-width: 10rem; max-height: 10rem;")
                if not twitch_user_exists:
                    txt = "Please log in to twitch first"
                elif discord_connected:
                    txt = "Connected"
                else:
                    txt = "Log in"
                ui.label(txt).classes("text-m m-auto")

            if discord_connected:
                async def disconnect_discord():
                    await bot.unsubscribe(_sess_id())
                    await db.disconnect_discord(_sess_id())
                    ui.navigate.to("/streamer")

                ui.button(
                    "Disconnect Discord", color="negative", on_click=disconnect_discord
                ).props("flat size=sm").classes("q-mt-sm")

    # Manual invite + redemption history (only when fully set up)
    if twitch_user_exists and discord_connected:
        ui.separator().classes("q-my-lg")

        with ui.row().classes("window-width row justify-center items-center"):
            ui.label("Manual invite").classes("text-h5")

        with ui.row().classes("window-width row justify-center items-center q-mb-md"):
            manual_input = ui.input(placeholder="Twitch username").props("outlined dense")
            manual_input.on("keydown.enter", lambda _: asyncio.ensure_future(add_manual()))

            async def add_manual():
                username = manual_input.value.strip()
                if not username:
                    ui.notify("Please enter a Twitch username.", type="warning")
                    return
                if not sanitize.is_valid_twitch_username(username):
                    ui.notify(
                        "Invalid username — must be 1–25 alphanumeric characters or underscores.",
                        type="warning",
                    )
                    return
                viewer = await twitch.lookup_user_by_name(_sess_id(), username)
                if viewer is None:
                    ui.notify(f"Twitch user '{username}' not found.", type="negative")
                    return
                if await db.has_pending_redemption(_sess_id(), viewer["id"]):
                    ui.notify(
                        f"{viewer['login']} already has a pending invite.", type="warning"
                    )
                    return
                await db.add_manual_redemption(
                    _sess_id(), viewer["id"], viewer["login"]
                )
                manual_input.value = ""
                ui.notify(f"{viewer['login']} can now claim an invite at /redeem.", type="positive")
                new_rows = await _load_rows(_sess_id())
                redemptions_table.rows = new_rows
                redemptions_table.update()
                _refresh_stats(new_rows)
                load_more_btn.set_visibility(len(new_rows) == _page_limit)

            ui.button("Add", on_click=add_manual).props("color=primary")

        ui.separator().classes("q-my-lg")

        with ui.row().classes("window-width row justify-center items-center q-mt-sm q-mb-xs"):
            ui.label("Redemption history").classes("text-h5")

        columns = [
            {"name": "viewer", "label": "Twitch User", "field": "viewer", "align": "left"},
            {"name": "type", "label": "Type", "field": "type", "align": "left"},
            {"name": "status", "label": "Status", "field": "status", "align": "left"},
            {"name": "redeemed_at", "label": "Added", "field": "redeemed_at", "align": "left"},
            {"name": "fulfilled_at", "label": "Fulfilled", "field": "fulfilled_at", "align": "left"},
            {"name": "invite_url", "label": "Invite Link", "field": "invite_url", "align": "left"},
            {"name": "actions", "label": "", "field": "actions", "align": "center"},
        ]

        def _fmt_dt(dt):
            return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"

        def _row_status(r):
            if r["revoked_at"]:
                return "Expired" if r.get("is_expired") else "Revoked"
            if r["fulfilled_at"]:
                return "Fulfilled"
            return "Pending"

        _page_limit = 200

        async def _load_rows(sess_id):
            records = await db.get_redemptions_for_streamer(sess_id, limit=_page_limit)
            rows = []
            for r in records:
                rows.append({
                    "id": r["id"],
                    "viewer": r["viewer_twitch_user_name"] or r["viewer_twitch_user_id"],
                    "type": "Manual" if r["is_manual"] else "Channel Point",
                    "status": _row_status(r),
                    "redeemed_at": _fmt_dt(r["redeemed_at"]),
                    "fulfilled_at": _fmt_dt(r["fulfilled_at"]),
                    "invite_url": r["invite_url"] or "—",
                    "can_revoke": not r["revoked_at"] and not r["fulfilled_at"],
                })
            return rows

        # Statistics row
        def _compute_stats(rows):
            return {
                "total": len(rows),
                "pending": sum(1 for r in rows if r["status"] == "Pending"),
                "fulfilled": sum(1 for r in rows if r["status"] == "Fulfilled"),
                "revoked": sum(1 for r in rows if r["status"] == "Revoked"),
                "expired": sum(1 for r in rows if r["status"] == "Expired"),
            }

        rows = await _load_rows(_sess_id())
        stats = _compute_stats(rows)

        with ui.row().classes("window-width row justify-center items-center q-mb-sm gap-xl"):
            stat_total = ui.label(f"Total: {stats['total']}").classes("text-body2")
            stat_pending = ui.label(f"Pending: {stats['pending']}").classes("text-body2 text-warning")
            stat_fulfilled = ui.label(f"Fulfilled: {stats['fulfilled']}").classes("text-body2 text-positive")
            stat_revoked = ui.label(f"Revoked: {stats['revoked']}").classes("text-body2 text-negative")
            stat_expired = ui.label(f"Expired: {stats['expired']}").classes("text-body2 text-negative")

        # Bulk-revoke button
        _current_sess = _sess_id()

        with ui.dialog() as bulk_revoke_dialog, ui.card().classes("q-pa-md"):
            ui.label("Revoke all pending invites").classes("text-h6 text-negative")
            ui.label(
                "This will revoke all pending (unclaimed) redemptions and attempt to "
                "invalidate any associated Discord invite links."
            ).classes("q-mt-sm text-body2")
            with ui.row().classes("justify-end q-mt-lg gap-sm"):
                ui.button("Cancel", on_click=bulk_revoke_dialog.close).props("flat")

                async def _confirm_bulk_revoke():
                    bulk_revoke_dialog.close()
                    revoked = await db.revoke_all_pending_redemptions(_current_sess)
                    new_rows = await _load_rows(_current_sess)
                    redemptions_table.rows = new_rows
                    redemptions_table.update()
                    _refresh_stats(new_rows)
                    load_more_btn.set_visibility(len(new_rows) == _page_limit)
                    ui.notify(
                        f"Revoked {len(revoked)} pending redemption(s).", type="positive"
                    )

                ui.button("Revoke all", color="negative", on_click=_confirm_bulk_revoke)

        with ui.row().classes("window-width row justify-center items-center q-mb-md gap-sm"):
            ui.button(
                "Revoke all pending", icon="block", color="negative",
                on_click=bulk_revoke_dialog.open,
            ).props("outline size=sm")

        redemptions_table = ui.table(columns=columns, rows=rows, row_key="id").classes(
            "window-width q-px-xl"
        )
        redemptions_table.add_slot("body-cell-actions", """
            <q-td :props="props">
                <q-btn v-if="props.row.can_revoke"
                    flat dense color="negative" label="Revoke" size="sm"
                    @click="$parent.$emit('revoke', props.row)" />
            </q-td>
        """)
        redemptions_table.add_slot("body-cell-invite_url", """
            <q-td :props="props">
                <a v-if="props.row.invite_url !== '—'"
                   :href="props.row.invite_url" target="_blank"
                   style="color: inherit;">{{ props.row.invite_url }}</a>
                <span v-else>—</span>
            </q-td>
        """)
        redemptions_table.add_slot("body-cell-status", """
            <q-td :props="props">
                <span :class="{
                    'text-positive': props.row.status === 'Fulfilled',
                    'text-warning':  props.row.status === 'Pending',
                    'text-negative': props.row.status === 'Revoked' || props.row.status === 'Expired'
                }" style="font-weight: 500;">{{ props.row.status }}</span>
            </q-td>
        """)

        def _refresh_stats(current_rows):
            s = _compute_stats(current_rows)
            stat_total.set_text(f"Total: {s['total']}")
            stat_pending.set_text(f"Pending: {s['pending']}")
            stat_fulfilled.set_text(f"Fulfilled: {s['fulfilled']}")
            stat_revoked.set_text(f"Revoked: {s['revoked']}")
            stat_expired.set_text(f"Expired: {s['expired']}")

        async def handle_revoke(e):
            rid = e.args.get("id")
            if not sanitize.is_positive_int(rid):
                ui.notify("Invalid request.", type="negative")
                return
            ok = await db.revoke_redemption(int(rid), _current_sess)
            if not ok:
                ui.notify("Could not revoke — already fulfilled or revoked.", type="warning")
            new_rows = await _load_rows(_current_sess)
            redemptions_table.rows = new_rows
            redemptions_table.update()
            _refresh_stats(new_rows)
            load_more_btn.set_visibility(len(new_rows) == _page_limit)

        redemptions_table.on("revoke", handle_revoke)

        # "Load more" — visible only when the table is at its current limit,
        # indicating additional rows may exist in the DB.
        async def _load_more():
            nonlocal _page_limit
            _page_limit += 200
            new_rows = await _load_rows(_current_sess)
            redemptions_table.rows = new_rows
            redemptions_table.update()
            _refresh_stats(new_rows)
            load_more_btn.set_visibility(len(new_rows) == _page_limit)

        with ui.row().classes("window-width row justify-center q-mt-sm"):
            load_more_btn = ui.button(
                "Load more", icon="expand_more", on_click=_load_more
            ).props("flat size=sm")
        load_more_btn.set_visibility(len(rows) == _page_limit)

        # Auto-refresh every 30 seconds
        async def _auto_refresh():
            new_rows = await _load_rows(_current_sess)
            redemptions_table.rows = new_rows
            redemptions_table.update()
            _refresh_stats(new_rows)
            load_more_btn.set_visibility(len(new_rows) == _page_limit)

        ui.timer(30, _auto_refresh)

    # -----------------------------------------------------------------------
    # Delete all data (only shown when the user has a Twitch account connected)
    # -----------------------------------------------------------------------
    if twitch_user_exists:
        ui.separator().classes("q-my-xl")

        with ui.dialog() as delete_dialog, ui.card().classes("q-pa-md"):
            ui.label("Delete all data").classes("text-h6 text-negative")
            ui.label(
                "This will permanently delete your account and all redemption records. "
                "This action cannot be undone."
            ).classes("q-mt-sm text-body2")
            with ui.row().classes("justify-end q-mt-lg gap-sm"):
                ui.button("Cancel", on_click=delete_dialog.close).props("flat")

                async def _confirm_delete():
                    delete_dialog.close()
                    await bot.unsubscribe(_sess_id())
                    await db.delete_user_and_all_records(_sess_id())
                    app.storage.user.clear()
                    ui.navigate.to("/")

                ui.button("Delete everything", color="negative", on_click=_confirm_delete)

        with ui.row().classes("window-width row justify-center items-center q-mb-xl"):
            ui.button(
                "Delete all data", icon="delete_forever", color="negative",
                on_click=delete_dialog.open,
            ).props("outline")

    footer()


@ui.page("/api/twitch/auth_code", dark=True)
async def twitch_page(request: Request):
    if _is_rate_limited(request):
        app.storage.user["error"] = "Too many attempts. Please wait a minute and try again."
        ui.navigate.to("/streamer")
        return

    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Connecting your account...").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.spinner(size="lg")

    # Check for errors, state mismatch, no code provided.
    if "error" in app.storage.user and app.storage.user["error"] is not None:
        ui.navigate.to("/streamer")
        return
    if app.storage.user.get("state") is None or request.query_params.get("state") != app.storage.user.get("state"):
        app.storage.user["error"] = "Invalid state"
        ui.navigate.to("/streamer")
        return
    if "code" not in request.query_params:
        app.storage.user["error"] = "Twitch did not provide an auth code."
        ui.navigate.to("/streamer")
        return

    # Consume the state token (one-time use)
    app.storage.user.pop("state", None)

    code = request.query_params["code"]

    res, err_msg = await twitch.init_login(_sess_id(), code)
    if not res:
        app.storage.user["error"] = err_msg
        ui.navigate.to("/streamer")
        return

    # Rotate the session after successful authentication to prevent fixation.
    old_id, new_id = await _rotate_session_id()

    # Resubscribe under the new session ID.
    await bot.unsubscribe(old_id)
    user = await db.get_user_by_session_id(new_id)
    if user and user.get("discord_server_id"):
        asyncio.create_task(bot.subscribe(user))

    # Beta gate — redirect non-allowlisted streamers to the waitlist and
    # delete everything written to the DB during this OAuth round-trip so
    # no data is retained for users who are not permitted.
    if user and not _is_beta_user(user.get("twitch_user_name", "")):
        twitch_username = user.get("twitch_user_name", "")
        await bot.unsubscribe(new_id)
        await db.delete_user_and_all_records(new_id)
        app.storage.user.clear()
        app.storage.user["waitlist_twitch"] = twitch_username
        ui.navigate.to("/waitlist")
        return

    ui.navigate.to("/streamer")
    return


@ui.page("/api/discord", dark=True)
async def discord_page(request: Request):
    if _is_rate_limited(request):
        app.storage.user["error"] = "Too many attempts. Please wait a minute and try again."
        ui.navigate.to("/streamer")
        return

    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Connecting your account...").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.spinner(size="lg")

    # CSRF state validation
    if (
        app.storage.user.get("discord_state") is None
        or request.query_params.get("state") != app.storage.user.get("discord_state")
    ):
        app.storage.user["error"] = "Invalid state parameter."
        ui.navigate.to("/streamer")
        return

    # Consume the state token (one-time use)
    app.storage.user.pop("discord_state", None)

    if "code" not in request.query_params:
        app.storage.user["error"] = "Discord did not provide an auth code."
        ui.navigate.to("/streamer")
        return
    if "guild_id" not in request.query_params:
        app.storage.user["error"] = "Discord did not provide a guild id."
        ui.navigate.to("/streamer")
        return

    code = request.query_params["code"]
    guild_id = request.query_params["guild_id"]
    if not sanitize.is_valid_snowflake(guild_id):
        app.storage.user["error"] = "Invalid guild id."
        ui.navigate.to("/streamer")
        return
    res, err_msg = await discorddb.update_info(
        _sess_id(), code, guild_id
    )
    if not res:
        app.storage.user["error"] = err_msg
        ui.navigate.to("/streamer")
        return

    # Rotate the session after successful authentication to prevent fixation.
    old_id, new_id = await _rotate_session_id()

    # Resubscribe under the new session ID.
    await bot.unsubscribe(old_id)
    user = await db.get_user_by_session_id(new_id)
    if user and user.get("twitch_user_id"):
        asyncio.create_task(bot.subscribe(user))

    ui.navigate.to("/streamer")
    return


@ui.page("/logout", dark=True)
async def logout_page():
    """Invalidate the current session and return to the home page."""
    app.storage.user.clear()
    ui.navigate.to("/")


@ui.page("/redeem", dark=True)
async def redeem_page():
    error = app.storage.user.pop("viewer_error", None)

    async def twitch_viewer_login():
        state = secrets.token_hex(32)
        app.storage.user["viewer_state"] = state
        ui.navigate.to(twitch.generate_viewer_auth_link(state))

    if error:
        ui.notify(error, type="negative", timeout=0)

    header()
    with ui.element("div").classes("th-page-wrap"):
        ui.html('<p class="th-label" style="margin-bottom: var(--space-4);">Viewer Login</p>', sanitize=False)
        with ui.element("div").classes("th-card"):
            ui.html(
                '<h2 class="th-card-title">Claim your Discord invite</h2>'
                '<p class="th-card-body">Sign in with Twitch to verify your identity '
                "and claim your one-time invite link.</p>",
                sanitize=False,
            )
            with ui.button(on_click=twitch_viewer_login, color=None).props(
                "no-caps unelevated"
            ).style(
                "width: 100%; background: #6441a5; border-radius: var(--radius-sm); "
                "padding: 10px 20px; color: white; font-family: var(--font-body); "
                "font-weight: 600; font-size: var(--fs-14);"
            ):
                ui.image("/static/img/TwitchGlitchWhite.svg").props(
                    "fit=scale-down"
                ).style("width: 24px; height: 24px; margin-right: 8px;")
                ui.label("Sign in with Twitch")
    footer()


@ui.page("/api/twitch/viewer_auth", dark=True)
async def viewer_auth_page(request: Request):
    if _is_rate_limited(request):
        app.storage.user["viewer_error"] = "Too many attempts. Please wait a minute and try again."
        ui.navigate.to("/redeem")
        return

    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Connecting your account...").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.spinner(size="lg")

    if app.storage.user.get("viewer_state") is None or request.query_params.get("state") != app.storage.user.get("viewer_state"):
        app.storage.user["viewer_error"] = "Invalid state. Please try again."
        ui.navigate.to("/redeem")
        return

    # Consume the state token immediately (one-time use, prevents replay)
    app.storage.user.pop("viewer_state", None)

    if "code" not in request.query_params:
        app.storage.user["viewer_error"] = "Twitch did not provide an auth code."
        ui.navigate.to("/redeem")
        return

    code = request.query_params["code"]
    token_info = await twitch.get_viewer_token(code)
    if token_info is None:
        app.storage.user["viewer_error"] = "Failed to verify your Twitch account."
        ui.navigate.to("/redeem")
        return

    user_info = await twitch.get_user_info(token_info["access_token"])
    # Revoke the token immediately — we only needed it to confirm identity.
    await twitch.revoke_token(token_info["access_token"])

    if user_info is None:
        app.storage.user["viewer_error"] = "Failed to get your Twitch user info."
        ui.navigate.to("/redeem")
        return

    viewer_user_id = user_info["id"]
    redemptions = await db.get_pending_redemptions_for_viewer(viewer_user_id)
    if not redemptions:
        app.storage.user["viewer_error"] = (
            "No pending redemptions found. Make sure you've redeemed the channel point first."
        )
        ui.navigate.to("/redeem")
        return

    if len(redemptions) == 1:
        redemption = redemptions[0]
        invite_url = await discorddb.create_invite(redemption["discord_server_id"])
        if invite_url is None:
            app.storage.user["viewer_error"] = "Failed to create your Discord invite. Please try again."
            ui.navigate.to("/redeem")
            return

        await db.fulfill_redemption(redemption["id"], invite_url)
        if redemption.get("twitch_redemption_id") and redemption.get("twitch_reward_id"):
            await twitch.fulfill_redemption(
                redemption["streamer_session_id"],
                redemption["twitch_reward_id"],
                redemption["twitch_redemption_id"],
            )
        ui.navigate.to(invite_url)
    else:
        # Multiple pending redemptions — let the viewer pick which streamer's
        # invite to claim.  Store just the display data in the session; the
        # full record is re-fetched and re-verified at claim time.
        app.storage.user["viewer_user_id"] = viewer_user_id
        app.storage.user["viewer_picks"] = [
            {"id": r["id"], "streamer_name": r["streamer_name"]}
            for r in redemptions
        ]
        ui.navigate.to("/redeem/pick")
    return


@ui.page("/redeem/pick", dark=True)
async def redeem_pick_page():
    """Shown when a viewer has pending redemptions from multiple streamers."""
    viewer_user_id = app.storage.user.get("viewer_user_id")
    if not viewer_user_id:
        ui.navigate.to("/redeem")
        return

    # Re-fetch from DB on every load so stale session data never shows
    # redemptions that have since been revoked.
    picks = await db.get_pending_redemptions_for_viewer(viewer_user_id)
    if not picks:
        app.storage.user.pop("viewer_user_id", None)
        app.storage.user.pop("viewer_picks", None)
        app.storage.user["viewer_error"] = "All your pending redemptions have been revoked."
        ui.navigate.to("/redeem")
        return

    header()
    with ui.column().classes("w-full items-center q-pa-xl").style(
        "max-width: 600px; margin: auto"
    ):
        ui.label("Choose your invite").classes("text-h4 text-center q-mb-md")
        ui.label(
            "You have pending redemptions from multiple streamers. "
            "Pick the one you'd like to claim."
        ).classes("text-body1 text-center q-mb-xl")

        options = {str(r["id"]): r["streamer_name"] for r in picks}
        sel = ui.select(options, label="Select a streamer").classes("w-full")

        status_label = ui.label("").classes("text-body2 text-center q-mt-sm")

        async def claim():
            chosen_id_str = sel.value
            if not chosen_id_str or not sanitize.is_positive_int(chosen_id_str):
                ui.notify("Please select a streamer.", type="warning")
                return

            chosen_id = int(chosen_id_str)

            # Re-query the DB to verify the redemption is still pending and
            # still belongs to this viewer (guards against session-stuffing and
            # redemptions that were revoked while the picker was open).
            all_pending = await db.get_pending_redemptions_for_viewer(viewer_user_id)
            redemption = next((r for r in all_pending if r["id"] == chosen_id), None)
            if redemption is None:
                ui.notify(
                    "That redemption is no longer available. Please try again.",
                    type="negative",
                )
                # Refresh the displayed options to reflect current state.
                remaining = [r for r in all_pending]
                if not remaining:
                    app.storage.user.pop("viewer_user_id", None)
                    app.storage.user.pop("viewer_picks", None)
                    app.storage.user["viewer_error"] = "All your pending redemptions have been revoked."
                    ui.navigate.to("/redeem")
                else:
                    app.storage.user["viewer_picks"] = [
                        {"id": r["id"], "streamer_name": r["streamer_name"]}
                        for r in remaining
                    ]
                    ui.navigate.to("/redeem/pick")
                return

            status_label.set_text("Creating your invite…")
            invite_url = await discorddb.create_invite(redemption["discord_server_id"])
            if invite_url is None:
                status_label.set_text("")
                ui.notify("Failed to create your Discord invite. Please try again.", type="negative")
                return

            await db.fulfill_redemption(redemption["id"], invite_url)
            if redemption.get("twitch_redemption_id") and redemption.get("twitch_reward_id"):
                await twitch.fulfill_redemption(
                    redemption["streamer_session_id"],
                    redemption["twitch_reward_id"],
                    redemption["twitch_redemption_id"],
                )

            app.storage.user.pop("viewer_user_id", None)
            app.storage.user.pop("viewer_picks", None)
            ui.navigate.to(invite_url)

        ui.button("Claim invite", on_click=claim, color="primary").classes("q-mt-lg")

    footer()


@ui.page("/contact", dark=True)
async def contact_page():
    _site_key = os.getenv("TURNSTILE_SITE_KEY", "")

    # Inject Turnstile script + token-capture callbacks into the page <head>.
    # data-execution="render" forces auto-execution on page load even for
    # invisible widget types, so the token is ready before the user submits.
    ui.add_head_html("""
        <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"
                async defer></script>
        <script>
        window._turnstileToken = null;
        function onTurnstileSuccess(t) { window._turnstileToken = t; }
        function onTurnstileExpired()  { window._turnstileToken = null; }
        </script>
    """)

    header()
    with ui.element("div").classes("th-page-wrap"):
        ui.html('<p class="th-label" style="margin-bottom: var(--space-4);">Get in Touch</p>', sanitize=False)
        with ui.element("div").classes("th-card"):
            ui.html(
                '<h2 class="th-card-title">Contact Us</h2>'
                '<p class="th-card-body">Have a question or feedback? '
                "Send us a message.</p>",
                sanitize=False,
            )

            name_input = (
                ui.input("Your name", placeholder="Sourk9")
                .props("outlined")
                .classes("w-full")
            )
            email_input = (
                ui.input("Your email", placeholder="you@example.com")
                .props("outlined")
                .classes("w-full")
            )
            message_input = (
                ui.textarea("Message", placeholder="Tell us what\u2019s on your mind\u2026")
                .props("outlined")
                .classes("w-full")
            )

            # Invisible Turnstile widget — renders with no visible UI.
            if _site_key:
                ui.html(
                    f'<div class="cf-turnstile"'
                    f' data-sitekey="{_site_key}"'
                    f' data-callback="onTurnstileSuccess"'
                    f' data-expired-callback="onTurnstileExpired"'
                    f' data-execution="render"'
                    f' data-size="invisible"></div>',
                    sanitize=False,
                )

            async def submit_contact():
                name = name_input.value.strip()
                email = email_input.value.strip()
                message = message_input.value.strip()

                if not name or not email or not message:
                    ui.notify("Please fill in all fields.", type="warning")
                    return
                if len(name) > 200:
                    ui.notify("Name is too long (max 200 characters).", type="warning")
                    return
                if not sanitize.is_valid_email(email):
                    ui.notify("Please enter a valid email address.", type="warning")
                    return
                if len(message) > 2000:
                    ui.notify(
                        "Message is too long (max 2000 characters).", type="warning"
                    )
                    return
                if _is_form_on_cooldown("contact"):
                    ui.notify(
                        "Please wait a moment before submitting again.", type="warning"
                    )
                    return

                # Verify Turnstile before touching any external service.
                if _site_key:
                    token = await ui.run_javascript(
                        "return window._turnstileToken || ''",
                        timeout=5.0,
                    )
                    if not await captcha.verify_turnstile(token):
                        ui.notify(
                            "Security check failed. Please try again.", type="warning"
                        )
                        ui.run_javascript(
                            "if (typeof turnstile !== 'undefined') turnstile.reset()"
                        )
                        return

                ok = await mail.send_contact_email(name, email, message)
                if ok:
                    _set_form_cooldown("contact")
                    name_input.value = ""
                    email_input.value = ""
                    message_input.value = ""
                    ui.notify("Message sent! We\u2019ll be in touch.", type="positive")
                else:
                    ui.notify(
                        "Failed to send your message. Please try again later.",
                        type="negative",
                    )

                # Reset Turnstile after each submission attempt so it can be
                # used again (token is single-use).
                if _site_key:
                    ui.run_javascript(
                        "if (typeof turnstile !== 'undefined') turnstile.reset()"
                    )

            ui.button("Send message", on_click=submit_contact, color=None).classes(
                "th-btn-primary q-mt-md"
            ).props("no-caps unelevated")

    footer()


@ui.page("/waitlist", dark=True)
async def waitlist_page():
    _site_key = os.getenv("TURNSTILE_SITE_KEY", "")

    ui.add_head_html("""
        <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"
                async defer></script>
        <script>
        window._turnstileToken = null;
        function onTurnstileSuccess(t) { window._turnstileToken = t; }
        function onTurnstileExpired()  { window._turnstileToken = null; }
        </script>
    """)

    header()
    with ui.element("div").classes("th-page-wrap"):
        ui.html('<p class="th-label" style="margin-bottom: var(--space-4);">Private Beta</p>', sanitize=False)
        with ui.element("div").classes("th-card"):
            ui.html(
                '<h2 class="th-card-title">Join the Waitlist</h2>'
                '<p class="th-card-body">Thinvite is currently in private beta. '
                "Enter your email and we\u2019ll notify you when access opens.</p>",
                sanitize=False,
            )

            # Pre-fill Twitch username when the beta gate sent the user here.
            prefill_twitch = app.storage.user.pop("waitlist_twitch", "") or ""

            email_input = (
                ui.input("Email address", placeholder="you@example.com")
                .props("outlined")
                .classes("w-full")
            )
            twitch_input = (
                ui.input("Twitch username (optional)", value=prefill_twitch)
                .props("outlined")
                .classes("w-full")
            )

            if _site_key:
                ui.html(
                    f'<div class="cf-turnstile"'
                    f' data-sitekey="{_site_key}"'
                    f' data-callback="onTurnstileSuccess"'
                    f' data-expired-callback="onTurnstileExpired"'
                    f' data-execution="render"'
                    f' data-size="invisible"></div>',
                    sanitize=False,
                )

            async def submit_waitlist():
                email = email_input.value.strip()
                twitch_username = twitch_input.value.strip()

                if not email:
                    ui.notify("Please enter your email address.", type="warning")
                    return
                if not sanitize.is_valid_email(email):
                    ui.notify("Please enter a valid email address.", type="warning")
                    return
                if twitch_username and not sanitize.is_valid_twitch_username(twitch_username):
                    ui.notify("Invalid Twitch username.", type="warning")
                    return
                if _is_form_on_cooldown("waitlist"):
                    ui.notify(
                        "Please wait a moment before submitting again.", type="warning"
                    )
                    return

                if _site_key:
                    token = await ui.run_javascript(
                        "return window._turnstileToken || ''",
                        timeout=5.0,
                    )
                    if not await captcha.verify_turnstile(token):
                        ui.notify(
                            "Security check failed. Please try again.", type="warning"
                        )
                        ui.run_javascript(
                            "if (typeof turnstile !== 'undefined') turnstile.reset()"
                        )
                        return

                ok = await mail.add_to_waitlist(email, twitch_username)
                if ok:
                    _set_form_cooldown("waitlist")
                    email_input.value = ""
                    twitch_input.value = ""
                    ui.notify(
                        "You\u2019re on the list! We\u2019ll email you when beta access opens.",
                        type="positive",
                    )
                else:
                    ui.notify(
                        "Failed to sign up. Please try again later.", type="negative"
                    )

                if _site_key:
                    ui.run_javascript(
                        "if (typeof turnstile !== 'undefined') turnstile.reset()"
                    )

            ui.button("Join waitlist", on_click=submit_waitlist, color=None).classes(
                "th-btn-primary q-mt-md"
            ).props("no-caps unelevated")

    footer()


@ui.page("/privacy", dark=True)
async def privacy_page():
    header()
    with ui.column().classes("w-full q-px-xl q-py-lg").style("max-width: 860px; margin: auto"):
        ui.label("Privacy Policy").classes("text-h3 q-mb-md")
        ui.label("Last updated: March 2026").classes("text-caption text-grey-6 q-mb-lg")

        def section(title: str, *lines: str):
            ui.label(title).classes("text-h5 q-mt-lg q-mb-sm")
            for line in lines:
                ui.label(line).classes("text-body1 q-mb-sm")

        section(
            "1. Overview",
            "Thinvite is a service operated by SourK9 Designs, LLC ('we', 'us', 'our') that links "
            "Twitch channel point redemptions to single-use Discord server invitations. This policy "
            "explains what data we collect, why we collect it, how it is used, and your rights under "
            "applicable data protection laws including the EU General Data Protection Regulation (GDPR).",
        )

        section(
            "2. Data Controller",
            "The data controller responsible for your personal data is SourK9 Designs, LLC. "
            "For data protection enquiries, contact us via Discord: @sourdusk.",
        )

        section(
            "3. Data We Collect",
            "Everyone: A single strictly necessary session cookie used to maintain your authentication "
            "state across pages. No advertising or tracking cookies are used.",
            "Streamers: Twitch username, Twitch user ID, Twitch OAuth access and refresh tokens "
            "(used to send chat messages and listen for channel point events), the channel point "
            "redeem ID you select, your Discord user ID, and your Discord server (guild) ID.",
            "Viewers: Your Twitch username and Twitch user ID are recorded at the moment you claim "
            "an invite. The temporary Twitch OAuth token is revoked immediately after your identity "
            "is confirmed — we do not store it.",
            "Redemption records: viewer Twitch username, associated streamer, timestamps "
            "(redeemed, fulfilled, revoked), and the Discord invite URL generated.",
        )

        section(
            "4. Legal Basis for Processing (GDPR Article 6)",
            "Contractual necessity (Art. 6(1)(b)): Processing your account data (Twitch and Discord "
            "credentials) is necessary to provide the Thinvite service you have requested.",
            "Legitimate interests (Art. 6(1)(f)): We retain redemption records to maintain an audit "
            "trail, prevent abuse, and resolve disputes. This is balanced against your privacy rights.",
            "Strictly necessary: The session cookie is strictly necessary for the technical operation "
            "of the service and is exempt from consent requirements under applicable cookie laws.",
        )

        section(
            "5. How We Use Your Data",
            "To maintain your authentication state between page loads.",
            "To create single-use Discord server invitations when a viewer redeems the configured "
            "channel point.",
            "To send an automated Twitch chat message directing the viewer to the /redeem page.",
            "To keep an audit trail of redemption activity for abuse prevention.",
            "We do not sell, rent, or share your data with any third party beyond what is required "
            "to operate the service (Twitch API and Discord API).",
        )

        section(
            "6. Data Retention",
            "Session data: Retained for the duration of your browser session or until you clear "
            "your cookies.",
            "Streamer account data: Retained until you delete your account via the /streamer page.",
            "Redemption records: Retained for up to 2 years from the date of redemption, "
            "then permanently deleted.",
            "You may request deletion of all your data at any time using the delete option at "
            "the bottom of the /streamer page.",
        )

        section(
            "7. International Data Transfers",
            "Thinvite integrates with Twitch (operated by Twitch Interactive, Inc., a subsidiary of "
            "Amazon.com, Inc.) and Discord (operated by Discord Inc.), both headquartered in the "
            "United States. When we interact with their APIs, your data may be transferred to and "
            "processed in the United States. These companies maintain their own privacy policies and "
            "data transfer safeguards.",
        )

        section(
            "8. Third-Party Services",
            "Twitch — used for streamer authentication, channel point event subscriptions, and "
            "viewer identity verification. Twitch's own privacy policy applies to data shared with "
            "their platform.",
            "Discord — used to create server invitations via the Discord Bot API. Discord's own "
            "privacy policy applies to data shared with their platform.",
        )

        section(
            "9. Cookies",
            "We use a single session cookie to maintain your authenticated state. This cookie is "
            "strictly necessary for the service to function and does not track you across other "
            "websites. No analytics, advertising, or third-party tracking cookies are used.",
        )

        section(
            "10. Your Rights Under GDPR",
            "If you are located in the European Economic Area (EEA) or the United Kingdom, you have "
            "the following rights regarding your personal data:",
            "Right of access (Art. 15): Request a copy of the personal data we hold about you.",
            "Right to rectification (Art. 16): Request correction of inaccurate personal data.",
            "Right to erasure (Art. 17): Request deletion of your personal data. Use the delete "
            "button on the /streamer page, or contact us directly.",
            "Right to restriction of processing (Art. 18): Request that we limit how we use your data.",
            "Right to data portability (Art. 20): Request your data in a structured, machine-readable "
            "format where technically feasible.",
            "Right to object (Art. 21): Object to processing based on legitimate interests.",
            "To exercise any of these rights, contact us via Discord: @sourdusk.",
        )

        section(
            "11. Right to Lodge a Complaint",
            "If you believe your data protection rights have been violated, you have the right to "
            "lodge a complaint with your local supervisory authority. For EU residents, this is the "
            "data protection authority in your member state. For UK residents, this is the "
            "Information Commissioner's Office (ICO) at ico.org.uk.",
        )

        section(
            "12. Contact",
            "For privacy questions or to exercise your rights under this policy, please contact "
            "SourK9 Designs, LLC via Discord: @sourdusk.",
        )

    footer()


# ---------------------------------------------------------------------------
# EventSub webhook — receives channel-point redemption events from Twitch
# ---------------------------------------------------------------------------

def _verify_eventsub_signature(
    msg_id: str, msg_timestamp: str, msg_sig: str, raw_body: bytes
) -> bool:
    """Verify the Twitch-Eventsub-Message-Signature HMAC-SHA256 header."""
    message = (msg_id + msg_timestamp).encode("utf-8") + raw_body
    expected = "sha256=" + hmac.new(
        _EVENTSUB_SECRET.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, msg_sig)


@app.post("/eventsub/callback")
async def eventsub_callback(request: Request):
    raw_body = await request.body()
    h = dict(request.headers)

    msg_id        = h.get("twitch-eventsub-message-id", "")
    msg_timestamp = h.get("twitch-eventsub-message-timestamp", "")
    msg_sig       = h.get("twitch-eventsub-message-signature", "")
    msg_type      = h.get("twitch-eventsub-message-type", "")

    # Reject messages older than 10 minutes (replay-attack prevention).
    try:
        ts = datetime.fromisoformat(msg_timestamp.replace("Z", "+00:00"))
        if abs((datetime.now(timezone.utc) - ts).total_seconds()) > 600:
            return Response(status_code=403)
    except Exception:
        return Response(status_code=400)

    # Reject messages with an invalid HMAC signature.
    if not _verify_eventsub_signature(msg_id, msg_timestamp, msg_sig, raw_body):
        return Response(status_code=403)

    # Deduplicate by message ID (Twitch may redeliver on network failure).
    # Persisted in DB so restarts don't open a redelivery window.
    if await db.is_seen_eventsub_message(msg_id):
        return Response(status_code=204)

    try:
        payload = json.loads(raw_body)
    except Exception:
        return Response(status_code=400)

    if msg_type == "webhook_callback_verification":
        # Twitch is verifying our endpoint — respond with the challenge string.
        return PlainTextResponse(payload.get("challenge", ""))

    if msg_type == "notification":
        asyncio.create_task(_handle_eventsub_event(payload))
        return Response(status_code=204)

    if msg_type == "revocation":
        asyncio.create_task(_handle_eventsub_revocation(payload))
        return Response(status_code=204)

    return Response(status_code=204)


async def _handle_eventsub_event(payload: dict) -> None:
    """Process a channel-point redemption notification from Twitch."""
    event = payload.get("event", {})
    broadcaster_id = event.get("broadcaster_user_id")
    if not broadcaster_id:
        return

    user = await db.get_user_by_twitch_id(broadcaster_id)
    if user is None:
        return

    sess_id  = user["session_id"]
    redeem_id = user.get("twitch_redeem_id")
    if not redeem_id:
        return

    reward = event.get("reward", {})
    if reward.get("id") != redeem_id:
        return  # not the reward we're watching

    redeemer             = event.get("user_name", "")
    viewer_id            = event.get("user_id", "")
    twitch_redemption_id = event.get("id", "")
    twitch_reward_id     = reward.get("id", "")
    broadcaster_user_id  = user["twitch_user_id"]
    token                = user["twitch_auth_token"]

    # Duplicate guard — cancel this redemption and refund points if the viewer
    # already has a pending one.
    if await db.has_pending_redemption(sess_id, viewer_id):
        logger.info(
            f"Duplicate redemption from {redeemer} for streamer {sess_id}; cancelling"
        )
        await twitch.cancel_redemption(
            broadcaster_user_id, twitch_reward_id, twitch_redemption_id, token
        )
        return

    await db.add_redemption(
        sess_id, viewer_id, redeemer, twitch_redemption_id, twitch_reward_id
    )

    site_url = _SITE_URL.rstrip("/")
    message = (
        f"@{redeemer} Head to {site_url}/redeem to claim your Discord invite! "
        "You have 24 hours before it expires."
    )
    await twitch.send_chat_message(broadcaster_user_id, broadcaster_user_id, message, token)


async def _handle_eventsub_revocation(payload: dict) -> None:
    """Handle a Twitch-initiated subscription revocation."""
    sub  = payload.get("subscription", {})
    sub_id       = sub.get("id", "unknown")
    broadcaster_id = sub.get("condition", {}).get("broadcaster_user_id")
    if broadcaster_id:
        user = await db.get_user_by_twitch_id(broadcaster_id)
        if user:
            await bot.handle_revocation(user["session_id"])
    logger.warning(
        f"EventSub subscription {sub_id} revoked by Twitch "
        f"(reason: {sub.get('status', 'unknown')})"
    )


# ---------------------------------------------------------------------------
# Static files & lifecycle
# ---------------------------------------------------------------------------
app.add_static_files('/static', 'static')


@app.on_startup
async def startup():
    if not os.getenv("NICEGUI_STORAGE_SECRET"):
        raise RuntimeError("NICEGUI_STORAGE_SECRET must be set before starting")
    if not _SITE_URL:
        raise RuntimeError("SITE_URL must be set before starting")
    if not _EVENTSUB_SECRET:
        raise RuntimeError("THINVITE_EVENTSUB_SECRET must be set before starting")
    await db.init_pool()         # pool must be ready before any DB call
    await bot.recover_subscriptions()
    asyncio.create_task(expiry.start_expiry_loop())


@app.on_shutdown
async def shutdown():
    await db.close_pool()


# ---------------------------------------------------------------------------
# Global <head> injections — applied to every page
# ---------------------------------------------------------------------------
def _roboto_font_display_css() -> str:
    """Return a <style> block that overrides font-display for Roboto @font-face rules.

    Reads NiceGUI's vendored fonts.css at startup, extracts every Roboto
    @font-face block, and re-emits them with font-display:swap added.
    The browser deduplicates @font-face rules by matching descriptors and
    uses the last declaration, so this wins without modifying the venv file.

    The relative url(fonts/...) references in fonts.css are rewritten to the
    absolute /_nicegui/{version}/static/fonts/... path so they resolve correctly
    when the rules are inlined into a page <style> block rather than loaded
    as an external stylesheet.
    """
    import nicegui as _nicegui
    fonts_css = pathlib.Path(_nicegui.__file__).parent / "static" / "fonts.css"
    try:
        text = fonts_css.read_text()
    except FileNotFoundError:
        return ""
    blocks = re.findall(
        r'@font-face\s*\{[^}]*font-family:\s*"Roboto"[^}]*\}',
        text,
        re.DOTALL,
    )
    if not blocks:
        return ""
    static_base = f"/_nicegui/{_nicegui.__version__}/static/"
    patched = [
        re.sub(r'url\(fonts/', f"url({static_base}fonts/", b).rstrip().rstrip("}")
        + "\n  font-display: swap;\n}"
        for b in blocks
    ]
    return "<style>\n" + "\n".join(patched) + "\n</style>"


# Favicon
ui.add_head_html(
    '<link rel="icon" type="image/svg+xml" href="/static/img/favicon.svg">',
    shared=True,
)

# Brand fonts (Syne for headings/wordmark, DM Sans for body)
ui.add_head_html(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Syne:wght@600;700;800'
    "&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;1,300;1,400"
    '&display=swap">',
    shared=True,
)

# Brand design tokens (CSS custom properties)
ui.add_head_html(
    '<link rel="stylesheet" href="/static/css/brand-tokens.css">',
    shared=True,
)

# Brand overrides and page utilities
ui.add_head_html(
    '<link rel="stylesheet" href="/static/css/brand.css">',
    shared=True,
)

ui.add_head_html(
    '<meta name="description" content="Twitch redeems -> Discord access. Securely.">',
    shared=True,
)

ui.add_head_html(
    '<meta property="og:title" content="Thinvite \u2014 Secure Discord Invites via Twitch">'
    '<meta property="og:description" content="Thinvite links Twitch channel point redemptions'
    " to single-use Discord server invites \u2014 reward your viewers instantly.\">"
    '<meta property="og:type" content="website">'
    f'<meta property="og:url" content="{_SITE_URL}">'
    '<meta name="twitter:card" content="summary">'
    '<meta name="twitter:title" content="Thinvite \u2014 Secure Discord Invites via Twitch">'
    '<meta name="twitter:description" content="Thinvite links Twitch channel point redemptions'
    " to single-use Discord server invites \u2014 reward your viewers instantly.\">",
    shared=True,
)

_roboto_css = _roboto_font_display_css()
if _roboto_css:
    ui.add_head_html(_roboto_css, shared=True)


ui.run(
    port=8083,
    storage_secret=os.getenv("NICEGUI_STORAGE_SECRET"),
    show=False,
    title="Thinvite",
    forwarded_allow_ips="127.0.0.1",
    session_middleware_kwargs={"https_only": True},
    reload_excludes=["**/beta_users.txt"],
)
