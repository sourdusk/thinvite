import asyncio
import os
import random
import logging
import time
from collections import defaultdict

from dotenv import load_dotenv
from nicegui import app, ui
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


load_dotenv()

import bot
import discorddb
import twitch
import db
import sanitize

logger = logging.getLogger()


# ---------------------------------------------------------------------------
# Security headers — applied to every response
# ---------------------------------------------------------------------------
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.add_middleware(_SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Simple in-memory rate limiter for the viewer OAuth callback
# ---------------------------------------------------------------------------
_viewer_auth_hits: dict = defaultdict(list)
_RATE_WINDOW = 60   # seconds
_RATE_MAX = 10      # max attempts per window per IP


def _is_rate_limited(request: Request) -> bool:
    ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
    now = time.monotonic()
    hits = [t for t in _viewer_auth_hits[ip] if now - t < _RATE_WINDOW]
    hits.append(now)
    _viewer_auth_hits[ip] = hits
    return len(hits) > _RATE_MAX


# ---------------------------------------------------------------------------
# Shared UI chrome
# ---------------------------------------------------------------------------
def header():
    with ui.header(elevated=True).style(
        "background-color: black"
    ).classes("justify-begin"):
        ui.label("Thinvite by SourK9 Designs")


def footer():
    """Footer that also injects the cookie-notice banner when needed."""
    with ui.footer().style("background-color: black"):
        if not app.storage.user.get("cookie_consent"):
            with ui.row().classes(
                "w-full items-center justify-between q-px-md q-py-sm"
            ).style("border-bottom: 1px solid #333") as cookie_row:
                with ui.row().classes("items-center gap-sm"):
                    ui.label(
                        "This site uses a single strictly necessary session cookie to maintain your "
                        "authentication state. No tracking or advertising cookies are used."
                    ).classes("text-caption")
                    ui.link("Privacy Policy", "/privacy").classes(
                        "text-caption text-primary"
                    )

                async def _accept():
                    app.storage.user["cookie_consent"] = True
                    cookie_row.delete()

                ui.button("Got it", on_click=_accept).props("dense size=sm color=primary")

        with ui.row().classes("w-full justify-between items-center q-px-md q-py-xs"):
            ui.label("Copyright SourK9 Designs, LLC 2026").classes("text-caption")
            ui.link("Privacy Policy", "/privacy").classes("text-caption text-grey-6")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@ui.page("/", dark=True)
async def home_page():
    header()
    with ui.column().classes("w-full items-center justify-center q-pa-xl").style(
        "min-height: 70vh; gap: 1.5rem"
    ):
        ui.label("Make your discords more secure.").classes("text-h3 text-center")
        ui.label(
            "Thinvite is an open-source solution that allows you to securely invite users to your discord."
        ).classes("text-h6 text-center")
        ui.label(
            "Thinvite integrates twitch redeems and discord invites to limit the exposure of invite links, preventing malicious actors."
        ).classes("text-h6 text-center")
        with ui.row().style("gap: 2rem; margin-top: 1rem"):
            ui.button("I'm a streamer", on_click=lambda: ui.navigate.to("/begin")).classes(
                "size-xl text-h5"
            )
            ui.button("Redeem an invite", on_click=lambda: ui.navigate.to("/redeem")).classes(
                "size-xl text-h5"
            )
    footer()


@ui.page("/begin", dark=True)
async def begin_page():
    if "error" in app.storage.user and app.storage.user["error"] is not None:
        ui.notify(app.storage.user["error"])
        app.storage.user["error"] = None

    if bot.needs_reauth(app.storage.browser["id"]):
        ui.notify(
            "Please re-connect your Twitch account to enable chat replies.",
            type="warning",
            timeout=0,
        )

    res = await db.ensure_db_user(app.storage.browser["id"])
    if not res:
        app.storage.user["error"] = "Failed to create user"
        ui.navigate.to("/begin")
        return

    async def twitch_login():
        if "state" not in app.storage.user:
            state = "%030x" % random.randrange(16**64)  # nosec
            app.storage.user["state"] = state
        else:
            state = app.storage.user["state"]
        force = bot.needs_reauth(app.storage.browser["id"])
        ui.navigate.to(twitch.generate_auth_code_link(state, force_verify=force))

    header()
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Begin by logging in to both Twitch and Discord.").classes(
            "text-h3 text-center text-justify"
        )

    # Run both DB lookups in parallel — avoids two sequential round-trips.
    twitch_user_exists, user_record = await asyncio.gather(
        twitch.user_exists(app.storage.browser["id"]),
        db.get_user_by_session_id(app.storage.browser["id"]),
    )
    discord_connected = (
        user_record is not None and user_record.get("discord_user_id") is not None
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
                    await bot.stop_listener(app.storage.browser["id"])
                    await db.disconnect_twitch(app.storage.browser["id"])
                    ui.navigate.to("/begin")

                ui.button(
                    "Disconnect Twitch", color="negative", on_click=disconnect_twitch
                ).props("flat size=sm").classes("q-mt-sm")

                redeems = await twitch.get_channel_redeems(app.storage.browser["id"])

                current_redeem = await twitch.get_set_redeem(app.storage.browser["id"])
                if current_redeem is None and redeems:
                    current_redeem = (
                        next((id for id, title in redeems.items() if "discord" in title.lower()), None)
                        or next((id for id, title in redeems.items() if "server" in title.lower()), None)
                        or next(iter(redeems))
                    )
                    await twitch.update_twitch_redeem(app.storage.browser["id"], current_redeem)
                ui.label("Select the channel point redeem").classes("text-body2 text-center")
                ui.label("viewers must use to receive a Discord invite:").classes("text-body2 text-center")
                sel = ui.select(redeems, value=current_redeem).classes("fit-width")

                async def update_redeem():
                    if sel.value == current_redeem:
                        ui.notify("That redeem is already selected.", type="info")
                        return
                    res = await twitch.update_twitch_redeem(
                        app.storage.browser["id"], sel.value
                    )
                    if res:
                        ui.notify("Redeem changed!")
                    else:
                        app.storage.user["error"] = "Failed to update redeem"
                        ui.navigate.to("/begin")

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
            ui.navigate.to(
                f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=1&response_type=code&redirect_uri=https%3A%2F%2Fthinvite.sourk9.com%2Fapi%2Fdiscord&integration_type=0&scope=identify+bot"
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
                    await bot.stop_listener(app.storage.browser["id"])
                    await db.disconnect_discord(app.storage.browser["id"])
                    ui.navigate.to("/begin")

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
                viewer = await twitch.lookup_user_by_name(app.storage.browser["id"], username)
                if viewer is None:
                    ui.notify(f"Twitch user '{username}' not found.", type="negative")
                    return
                await db.add_manual_redemption(
                    app.storage.browser["id"], viewer["id"], viewer["login"]
                )
                manual_input.value = ""
                ui.notify(f"{viewer['login']} can now claim an invite at /redeem.", type="positive")
                redemptions_table.rows = await _load_rows(app.storage.browser["id"])
                redemptions_table.update()

            ui.button("Add", on_click=add_manual).props("color=primary")

        ui.separator().classes("q-my-lg")

        with ui.row().classes("window-width row justify-center items-center"):
            ui.label("Redemption history").classes("text-h5")

        columns = [
            {"name": "viewer", "label": "Twitch User", "field": "viewer", "align": "left"},
            {"name": "type", "label": "Type", "field": "type", "align": "left"},
            {"name": "status", "label": "Status", "field": "status", "align": "left"},
            {"name": "redeemed_at", "label": "Redeemed", "field": "redeemed_at", "align": "left"},
            {"name": "fulfilled_at", "label": "Fulfilled", "field": "fulfilled_at", "align": "left"},
            {"name": "invite_url", "label": "Invite", "field": "invite_url", "align": "left"},
            {"name": "actions", "label": "", "field": "actions", "align": "center"},
        ]

        def _fmt_dt(dt):
            return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"

        def _row_status(r):
            if r["revoked_at"]:
                return "Revoked"
            if r["fulfilled_at"]:
                return "Fulfilled"
            return "Pending"

        async def _load_rows(sess_id):
            records = await db.get_redemptions_for_streamer(sess_id)
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

        rows = await _load_rows(app.storage.browser["id"])
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

        async def handle_revoke(e):
            rid = e.args.get("id")
            if not sanitize.is_positive_int(rid):
                ui.notify("Invalid request.", type="negative")
                return
            await db.revoke_redemption(int(rid), app.storage.browser["id"])
            redemptions_table.rows = await _load_rows(app.storage.browser["id"])
            redemptions_table.update()

        redemptions_table.on("revoke", handle_revoke)

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
                    await bot.stop_listener(app.storage.browser["id"])
                    await db.delete_user_and_all_records(app.storage.browser["id"])
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
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Hang tight while we gather some information...").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.spinner(size="lg")
    # Check for errors, state mismatch, no code provided.
    if "error" in app.storage.user and app.storage.user["error"] is not None:
        ui.navigate.to("/begin")
        return
    if app.storage.user.get("state") is None or request.query_params["state"] != app.storage.user["state"]:
        app.storage.user["error"] = "Invalid state"
        ui.navigate.to("/begin")
        return
    if "code" not in request.query_params:
        app.storage.user["error"] = "Twitch did not provide an auth code."
        ui.navigate.to("/begin")
        return

    code = request.query_params["code"]

    res, err_msg = await twitch.init_login(app.storage.browser["id"], code)
    if not res:
        app.storage.user["error"] = err_msg
        ui.navigate.to("/begin")
        return

    # Try to start bot listener now that the user has (re-)authenticated with Twitch.
    # Fire as a background task so the WebSocket handshake doesn't block the page response.
    user = await db.get_user_by_session_id(app.storage.browser["id"])
    if user and user.get("discord_server_id"):
        asyncio.create_task(bot.start_listener(user))

    ui.navigate.to("/begin")
    return


@ui.page("/api/discord", dark=True)
async def discord_page(request: Request):
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Hang tight while we gather some information...").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.spinner(size="lg")
    if "code" not in request.query_params:
        app.storage.user["error"] = "Discord did not provide an auth code."
        ui.navigate.to("/begin")
        return
    if "guild_id" not in request.query_params:
        app.storage.user["error"] = "Discord did not provide a guild id."
        ui.navigate.to("/begin")
        return

    code = request.query_params["code"]
    guild_id = request.query_params["guild_id"]
    res, err_msg = await discorddb.update_info(
        app.storage.browser["id"], code, guild_id
    )
    if not res:
        app.storage.user["error"] = err_msg
        ui.navigate.to("/begin")
        return

    # Fire as a background task so the WebSocket handshake doesn't block the page response.
    user = await db.get_user_by_session_id(app.storage.browser["id"])
    if user:
        asyncio.create_task(bot.start_listener(user))
    ui.navigate.to("/begin")
    return


@ui.page("/redeem", dark=True)
async def redeem_page():
    error = app.storage.user.pop("viewer_error", None)

    header()
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Claim your Discord invite").classes("text-h3 text-center")

    if error:
        ui.notify(error, type="negative", timeout=0)

    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Log in with Twitch to verify your identity and claim your invite.").classes(
            "text-h6 text-center"
        )

    async def twitch_viewer_login():
        state = "%030x" % random.randrange(16**64)  # nosec
        app.storage.user["viewer_state"] = state
        ui.navigate.to(twitch.generate_viewer_auth_link(state))

    with ui.row().classes("window-width row justify-center items-center"):
        with ui.button(color="#6441a5", on_click=twitch_viewer_login).style(
            "width: 10rem; height: 15rem;"
        ):
            ui.image("/static/img/TwitchGlitchWhite.svg").props(
                "fit=scale-down"
            ).classes("m-auto").style("max-width: 10rem; max-height: 10rem;")
            ui.label("Log in").classes("text-m m-auto")
    footer()


@ui.page("/api/twitch/viewer_auth", dark=True)
async def viewer_auth_page(request: Request):
    if _is_rate_limited(request):
        app.storage.user["viewer_error"] = "Too many attempts. Please wait a minute and try again."
        ui.navigate.to("/redeem")
        return

    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Hang tight while we gather some information...").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.spinner(size="lg")

    if app.storage.user.get("viewer_state") is None or request.query_params.get("state") != app.storage.user.get("viewer_state"):
        app.storage.user["viewer_error"] = "Invalid state. Please try again."
        ui.navigate.to("/redeem")
        return
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

    redemption = redemptions[0]
    invite_url = await discorddb.create_invite(redemption["discord_server_id"])
    if invite_url is None:
        app.storage.user["viewer_error"] = "Failed to create your Discord invite. Please try again."
        ui.navigate.to("/redeem")
        return

    await db.fulfill_redemption(redemption["id"], invite_url)
    ui.navigate.to(invite_url)
    return


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
            "Streamer account data: Retained until you delete your account via the /begin page.",
            "Redemption records: Retained for up to 2 years from the date of redemption, "
            "then permanently deleted.",
            "You may request deletion of all your data at any time using the delete option at "
            "the bottom of the /begin page.",
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
            "button on the /begin page, or contact us directly.",
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
# Static files & lifecycle
# ---------------------------------------------------------------------------
app.add_static_files('/static', 'static')


@app.on_startup
async def startup():
    await db.init_pool()         # pool must be ready before any DB call
    await bot.start_all_listeners()


@app.on_shutdown
async def shutdown():
    await db.close_pool()


ui.run(
    port=8083,
    storage_secret=os.getenv("NICEGUI_STORAGE_SECRET"),
    show=False,
    title="Thinvite",
    forwarded_allow_ips="*",
)
