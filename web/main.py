import os
import random
import logging

from dotenv import load_dotenv
from nicegui import app, ui
from fastapi import Request


import discorddb
import twitch
import db

logger = logging.getLogger()


def header():
    with ui.header(elevated=True).style(
        "background-color: var(--primary-color)"
    ).classes("justify-begin"):
        ui.label("Thinvite by SourK9 Designs")


@ui.page("/", dark=True)
async def home_page():
    header()
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Make your discords more secure.").classes(
            "text-h3 text-center text-justify"
        )
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label(
            "Thinvite is an open-source solution that allows you to securely invite users to your discord."
        ).classes("text-h6 text-center")
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label(
            "Thinvite integrates twitch redeems and discord invites to limit the exposure of invite links, preventing malicious actors."
        ).classes("text-h6 text-center")
    with ui.row().classes(
        "window-width row justify-center items-center absolute-center"
    ):
        ui.button("Get Started", on_click=lambda: ui.navigate.to("/begin")).classes(
            "justify-center size-xl text-h5"
        )


@ui.page("/begin", dark=True)
async def begin_page():
    if "error" in app.storage.user and app.storage.user["error"] is not None:
        ui.notify(app.storage.user["error"])
        app.storage.user["error"] = None

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
        ui.navigate.to(twitch.generate_auth_code_link(state))

    header()
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Begin by logging in to both Twitch and Discord.").classes(
            "text-h3 text-center text-justify"
        )

    # Row with buttons
    with ui.row().classes("window-width row justify-center items-center").style(
        "gap: 15rem"
    ):  # Column 1 (twitch)
        twitch_user_exists = await twitch.user_exists(app.storage.browser["id"])
        # Twitch button after login
        if twitch_user_exists:
            with ui.column():
                with ui.button(color="#6441a5", on_click=twitch_login).style(
                    "width: 10rem; height: 15rem;"
                ):
                    with ui.column().style("gap: 0.1rem"):
                        ui.image("/static/img/TwitchGlitchWhite.svg").props(
                            "fit=scale-down"
                        ).classes("m-auto").style(
                            "max-width: 15rem; max-height: 15rem;"
                        )
                        ui.label("Logged in").classes("text-h5 m-auto")
                redeems = await twitch.get_channel_redeems(app.storage.browser["id"])

                current_redeem = await twitch.get_set_redeem(app.storage.browser["id"])
                sel = ui.select(redeems, value=current_redeem).classes("fit-width")

                async def update_redeem():
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
            with ui.column():
                with ui.button(color="#6441a5", on_click=twitch_login).style(
                    "width: 10rem; height: 15rem;"
                ):
                    ui.image("static/img/TwitchGlitchWhite.svg").props(
                        "fit=scale-down"
                    ).classes("m-auto").style("max-width: 10rem; max-height: 10rem;")
                    ui.label("Log in").classes("text-h5 m-auto")

        # Column 2 (discord)
        async def discord_login():
            ui.navigate.to(
                "https://discord.com/oauth2/authorize?client_id=1207052127386599444&permissions=1&response_type=code&redirect_uri=https%3A%2F%2Fthinvite.sourk9.com%2Fapi%2Fdiscord&integration_type=0&scope=identify+bot"
            )

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
            else:
                txt = "Log in"
            ui.label(txt).classes("text-m m-auto")


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
    if request.query_params["state"] != app.storage.user["state"]:
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

    # Everything happened!
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

    # Everything happened!
    ui.navigate.to("/begin")
    return


load_dotenv()


ui.run(
    port=8083,
    storage_secret=os.getenv("NICEGUI_STORAGE_SECRET"),
    show=False,
    title="Thinvite",
)
