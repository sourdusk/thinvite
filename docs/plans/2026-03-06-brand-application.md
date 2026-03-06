# Brand Application Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Apply the Thinvite brand identity (brand-tokens.css + brand-guidelines.html) to the live NiceGUI site — fully redesigning public pages, token-level styling on functional pages.

**Architecture:** CSS globals (brand-tokens + brand overrides) loaded via shared head injections; `ui.html()` for non-interactive visual sections on public pages; NiceGUI widgets for all interactive elements. Quasar's CSS variables (`--q-primary` etc.) are overridden to wire brand colours into existing widget props.

**Tech Stack:** NiceGUI (Quasar under the hood), Python, CSS custom properties, Google Fonts (Syne + DM Sans)

---

## Task 1: Copy brand-tokens.css to static

**Files:**
- Create: `web/static/css/brand-tokens.css`

**Step 1: Copy the file**

```bash
cp /opt/websites/thinvite_sourk9_com/docs/brand-tokens.css \
   /opt/websites/thinvite_sourk9_com/web/static/css/brand-tokens.css
```

**Step 2: Verify**

```bash
ls -la /opt/websites/thinvite_sourk9_com/web/static/css/
```
Expected: `brand-tokens.css` present.

---

## Task 2: Write brand.css

**Files:**
- Create: `web/static/css/brand.css`

**Step 1: Create the file with full content**

```css
/* Thinvite — Brand Overrides & Utilities
 * Loaded after brand-tokens.css. Overrides Quasar defaults and
 * defines reusable brand classes used by page functions.
 */

/* ── QUASAR THEME VARIABLES ─────────────────────────────────────── */
/* Wire brand tokens into Quasar's CSS variable system so that
 * NiceGUI props like color="primary" resolve to brand colours. */
:root {
  --q-primary:   #7c5af0;
  --q-secondary: #3d6ef5;
  --q-accent:    #4ecdc4;
  --q-positive:  #4ecdc4;
  --q-dark:      #1c1c30;
  --q-dark-page: #0d0d14;
}

/* ── BODY BASE ──────────────────────────────────────────────────── */
body {
  font-family: var(--font-body);
}

.body--dark {
  background: var(--color-ink);
  color: var(--color-frost);
}

/* ── HEADER / FOOTER ────────────────────────────────────────────── */
.q-header {
  background: var(--color-surface-2) !important;
  border-bottom: var(--border-default);
  box-shadow: none !important;
}

.q-footer {
  background: var(--color-surface) !important;
  border-top: var(--border-default);
}

/* ── QUASAR TYPOGRAPHY ──────────────────────────────────────────── */
/* Headings get Syne; body copy stays DM Sans via body rule above. */
.text-h1, .text-h2, .text-h3, .text-h4, .text-h5, .text-h6 {
  font-family: var(--font-display) !important;
  letter-spacing: var(--ls-tight) !important;
}

/* ── QUASAR BUTTONS ─────────────────────────────────────────────── */
.q-btn {
  font-family: var(--font-body) !important;
  border-radius: var(--radius-sm) !important;
  text-transform: none !important;
  letter-spacing: 0 !important;
}

/* Primary gradient button — apply with .classes('th-btn-primary') */
.q-btn.th-btn-primary {
  background: var(--grad-primary) !important;
  color: white !important;
  font-weight: var(--fw-semibold) !important;
  font-size: var(--fs-14) !important;
  box-shadow: var(--shadow-btn-primary) !important;
  padding: var(--space-btn-y) var(--space-btn-x) !important;
  border: none !important;
}

/* Secondary outline button — apply with .classes('th-btn-secondary') */
.q-btn.th-btn-secondary {
  background: transparent !important;
  color: var(--color-frost) !important;
  font-weight: var(--fw-medium) !important;
  font-size: var(--fs-14) !important;
  border: var(--border-violet-medium) !important;
  padding: var(--space-btn-y) var(--space-btn-x) !important;
  box-shadow: none !important;
}

/* ── QUASAR INPUTS ──────────────────────────────────────────────── */
.body--dark .q-field__control {
  background: rgba(255, 255, 255, 0.04);
}

.body--dark .q-field__label {
  color: var(--color-muted);
}

.body--dark .q-field__native,
.body--dark .q-field__input {
  color: var(--color-frost);
}

.body--dark .q-field--outlined .q-field__control:before {
  border-color: var(--color-border);
}

.body--dark .q-field--outlined:hover .q-field__control:before {
  border-color: var(--color-violet-tint-40);
}

.body--dark .q-field--focused .q-field__control:after {
  border-color: var(--color-violet) !important;
}

/* ── QUASAR TABLES ──────────────────────────────────────────────── */
.body--dark .q-table th {
  font-family: var(--font-display);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-11);
  letter-spacing: var(--ls-widest-1);
  text-transform: uppercase;
  color: var(--color-muted);
}

.body--dark .q-table__card {
  background: var(--color-surface-2);
}

/* ── QUASAR SELECT ──────────────────────────────────────────────── */
.body--dark .q-menu {
  background: var(--color-surface-2);
  border: var(--border-default);
  border-radius: var(--radius-md);
}

/* ── QUASAR SEPARATOR ───────────────────────────────────────────── */
.q-separator {
  background: var(--color-border) !important;
}

/* ── QUASAR DIALOGS ─────────────────────────────────────────────── */
.body--dark .q-dialog .q-card {
  background: var(--color-surface-2);
  border: var(--border-default);
  border-radius: var(--radius-lg);
}

/* ── BRAND UTILITY CLASSES ──────────────────────────────────────── */

/* Gradient text — for wordmark accent and display italic */
.th-grad-text {
  background: var(--grad-primary);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* ── HERO SECTION ───────────────────────────────────────────────── */
.th-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  min-height: 80vh;
  padding: var(--space-16) var(--space-12);
  gap: var(--space-6);
  max-width: 800px;
  margin: 0 auto;
  box-sizing: border-box;
}

.th-display {
  font-family: var(--font-body);
  font-weight: var(--fw-light);
  font-size: var(--fs-40);
  letter-spacing: var(--ls-tightest);
  line-height: var(--lh-tight);
  color: var(--color-frost);
  margin: 0;
}

.th-display em {
  font-style: italic;
  font-weight: var(--fw-light);
  background: var(--grad-primary);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.th-heading {
  font-family: var(--font-display);
  font-weight: var(--fw-bold);
  font-size: var(--fs-24);
  letter-spacing: var(--ls-tight);
  color: var(--color-frost);
  margin: 0;
}

.th-body {
  font-family: var(--font-body);
  font-weight: var(--fw-regular);
  font-size: var(--fs-15);
  line-height: var(--lh-base);
  color: var(--color-body-text);
  max-width: 520px;
  margin: 0;
}

.th-label {
  font-family: var(--font-body);
  font-weight: var(--fw-medium);
  font-size: var(--fs-11);
  letter-spacing: var(--ls-widest-1);
  text-transform: uppercase;
  color: var(--color-violet-lt);
  margin: 0;
}

.th-cta-row {
  display: flex;
  gap: var(--space-5);
  flex-wrap: wrap;
  justify-content: center;
  margin-top: var(--space-2);
}

/* ── CARD COMPONENT ─────────────────────────────────────────────── */
.th-card {
  background: var(--color-surface-2);
  border: var(--border-default);
  border-radius: var(--radius-lg);
  padding: var(--space-12);
  max-width: 480px;
  width: 100%;
  box-sizing: border-box;
}

.th-card-title {
  font-family: var(--font-display);
  font-weight: var(--fw-bold);
  font-size: var(--fs-24);
  letter-spacing: var(--ls-tight);
  color: var(--color-frost);
  margin: 0 0 var(--space-3) 0;
}

.th-card-body {
  font-family: var(--font-body);
  font-weight: var(--fw-regular);
  font-size: var(--fs-15);
  line-height: var(--lh-base);
  color: var(--color-body-text);
  margin: 0 0 var(--space-6) 0;
}

/* ── HEADER WORDMARK ────────────────────────────────────────────── */
.th-wordmark {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  text-decoration: none;
  padding: var(--space-3) 0;
}

.th-wordmark-text {
  font-family: var(--font-display);
  font-weight: var(--fw-bold);
  font-size: var(--fs-22);
  letter-spacing: var(--ls-tight-sm);
  color: var(--color-frost);
  line-height: 1;
}

/* ── PAGE WRAPPER ───────────────────────────────────────────────── */
/* Used by card pages to vertically centre the card */
.th-page-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 80vh;
  padding: var(--space-12) var(--space-6);
  box-sizing: border-box;
  width: 100%;
}
```

**Step 2: Verify file exists**

```bash
ls -la /opt/websites/thinvite_sourk9_com/web/static/css/
```
Expected: both `brand-tokens.css` and `brand.css` present.

---

## Task 3: Update CSP in _SecurityHeadersMiddleware

**Files:**
- Modify: `web/main.py` (the `_SecurityHeadersMiddleware.__call__` method, ~line 88–100)

**Step 1: Locate the current style-src and font-src lines**

Search for `font-src` in main.py to find the exact line numbers.

**Step 2: Update the two lines**

Current:
```python
"style-src 'self' 'unsafe-inline'; "
"font-src 'self' data:; "
```

Replace with:
```python
"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
"font-src 'self' data: https://fonts.gstatic.com; "
```

---

## Task 4: Add global head injections to main.py

**Files:**
- Modify: `web/main.py` (the global `ui.add_head_html` block at the bottom, ~line 1645–1666)

**Step 1: Add three new `ui.add_head_html` calls with `shared=True` BEFORE the existing ones**

Insert immediately after the `_SITE_WSS` assignment (~line 66) — or more precisely, after all imports/constants but before page definitions. The best location is just above the first existing `ui.add_head_html(...)` call (near line 1645).

Add these three calls:

```python
# Brand fonts
ui.add_head_html(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Syne:wght@600;700;800'
    '&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,600;1,300;1,400'
    '&display=swap">',
    shared=True,
)

# Brand design tokens
ui.add_head_html(
    '<link rel="stylesheet" href="/static/css/brand-tokens.css">',
    shared=True,
)

# Brand overrides and utilities
ui.add_head_html(
    '<link rel="stylesheet" href="/static/css/brand.css">',
    shared=True,
)
```

---

## Task 5: Rewrite header() and footer()

**Files:**
- Modify: `web/main.py` — `header()` function (~line 267) and `footer()` function (~line 280)

**Step 1: Replace `header()`**

Current:
```python
def header(show_logout: bool = False):
    with ui.header(elevated=True).style(
        "background-color: black"
    ).classes("items-center justify-between"):
        ui.label("Thinvite by SourK9 Designs")
        if show_logout:
            ui.button(
                "Logout",
                icon="logout",
                on_click=lambda: ui.navigate.to("/logout"),
            ).props("flat color=white size=sm").classes("ml-auto")
```

Replace with:
```python
def header(show_logout: bool = False):
    with ui.header(elevated=False).classes("items-center justify-between").style(
        "padding: 0 var(--space-6);"
    ):
        ui.html(
            '<a href="/" class="th-wordmark">'
            '<svg width="32" height="32" viewBox="0 0 56 56" fill="none">'
            '<defs><linearGradient id="hdr-grad" x1="0" y1="0" x2="56" y2="56" gradientUnits="userSpaceOnUse">'
            '<stop offset="0%" stop-color="#7c5af0"/>'
            '<stop offset="100%" stop-color="#3d6ef5"/>'
            '</linearGradient></defs>'
            '<rect width="56" height="56" rx="14" fill="url(#hdr-grad)"/>'
            '<circle cx="17.75" cy="36.25" r="10" stroke="white" stroke-width="2.5" fill="none"/>'
            '<circle cx="41.75" cy="16.25" r="6.5" fill="white"/>'
            '<line x1="28.25" y1="27.25" x2="35.75" y2="21.75" stroke="white" stroke-width="2.5" stroke-linecap="round"/>'
            '</svg>'
            '<span class="th-wordmark-text">Thin<span class="th-grad-text">vite</span></span>'
            '</a>'
        )
        if show_logout:
            ui.button(
                "Logout",
                icon="logout",
                on_click=lambda: ui.navigate.to("/logout"),
            ).props("flat color=white size=sm").classes("ml-auto")
```

**Step 2: Replace `footer()`**

Current:
```python
def footer():
    """Footer that also injects the cookie-notice banner when needed."""
    with ui.footer().style("background-color: black"):
        if not app.storage.user.get("cookie_consent"):
            with ui.row().classes(
                "w-full items-center justify-between q-px-md q-py-sm"
            ).style("border-bottom: 1px solid #333") as cookie_row:
                ...
        with ui.row().classes("w-full justify-between items-center q-px-md q-py-xs"):
            ui.label("Copyright SourK9 Designs, LLC 2026").classes("text-caption")
            with ui.row().classes("gap-md"):
                ui.link("Contact", "/contact").classes("text-caption text-grey-6")
                ui.link("Privacy Policy", "/privacy").classes("text-caption text-grey-6")
```

Replace with (preserve ALL cookie-consent logic exactly, only change styling):
```python
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
            with ui.row().classes("gap-md"):
                ui.link("Contact", "/contact").classes("text-caption").style(
                    "color: var(--color-muted);"
                )
                ui.link("Privacy Policy", "/privacy").classes("text-caption").style(
                    "color: var(--color-muted);"
                )
```

---

## Task 6: Rewrite home_page()

**Files:**
- Modify: `web/main.py` — `home_page()` function (~line 339)

**Step 1: Replace the function body (keep decorator and signature)**

Current body:
```python
    header()
    with ui.column().classes("w-full items-center justify-center q-pa-xl").style(
        "min-height: 70vh; gap: 1.5rem"
    ):
        ui.label("Make your discords more secure.").classes("text-h3 text-center")
        ...
    footer()
```

Replace with:
```python
    header()
    with ui.element("div").style("width: 100%; display: flex; justify-content: center;"):
        ui.html(
            '<div class="th-hero">'
            '<p class="th-label">Channel Redeems &middot; Discord Invites &middot; Verified Access</p>'
            '<h1 class="th-display">Twitch redeems &rarr; Discord access. <em>Securely.</em></h1>'
            '<h2 class="th-heading">Your Twitch. Your Discord.</h2>'
            '<p class="th-body">Streamers set up a channel point redeem. Viewers sign in with Twitch '
            'and receive a unique, single-use invite link. No sharing, no abuse.</p>'
            '</div>'
        )
    with ui.element("div").classes("th-cta-row").style(
        "width: 100%; display: flex; justify-content: center; gap: var(--space-5); "
        "flex-wrap: wrap; padding-bottom: var(--space-22);"
    ):
        ui.button("I\u2019m a streamer", on_click=lambda: ui.navigate.to("/begin")).classes(
            "th-btn-primary"
        ).props("no-caps unelevated")
        ui.button("Redeem an invite", on_click=lambda: ui.navigate.to("/redeem")).classes(
            "th-btn-secondary"
        ).props("no-caps outline")
    footer()
```

---

## Task 7: Rewrite redeem_page()

**Files:**
- Modify: `web/main.py` — `redeem_page()` function (~line 909)

**Step 1: Replace the visual layout (preserve all logic)**

The function defines `twitch_viewer_login()` inside it. Preserve that closure exactly.

Current layout portion:
```python
    header()
    with ui.row().classes("window-width row justify-center items-center"):
        ui.label("Claim your Discord invite").classes("text-h3 text-center")
    if error:
        ui.notify(error, type="negative", timeout=0)
    with ui.row()...:
        ui.label("Log in with Twitch...").classes(...)
    with ui.row()...:
        with ui.button(color="#6441a5", ...).style("width: 10rem; height: 15rem;"):
            ui.image(...)
            ui.label("Log in")
    footer()
```

Replace layout portion with:
```python
    if error:
        ui.notify(error, type="negative", timeout=0)

    header()
    with ui.element("div").classes("th-page-wrap"):
        ui.html('<p class="th-label" style="margin-bottom: var(--space-4);">Viewer Login</p>')
        with ui.element("div").classes("th-card"):
            ui.html(
                '<h2 class="th-card-title">Claim your Discord invite</h2>'
                '<p class="th-card-body">Sign in with Twitch to verify your identity '
                'and claim your one-time invite link.</p>'
            )
            with ui.button(on_click=twitch_viewer_login).props(
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
```

Note: `error` pop and `twitch_viewer_login` definition remain at the top of the function unchanged.

---

## Task 8: Rewrite waitlist_page()

**Files:**
- Modify: `web/main.py` — `waitlist_page()` function (~line 1222)

**Step 1: Preserve the top block (Turnstile scripts, prefill logic, submit handler) — only replace the layout**

Current layout portion (after `ui.add_head_html` calls):
```python
    header()
    with ui.column().classes("w-full items-center q-pa-xl").style(
        "max-width: 600px; margin: auto"
    ):
        ui.label("Join the Waitlist").classes("text-h3 q-mb-md text-center")
        ui.label("Thinvite is currently...").classes("text-body1 q-mb-xl text-center")
        prefill_twitch = ...
        email_input = ui.input(...)
        twitch_input = ui.input(...)
        if _site_key: ui.html(...)      # turnstile widget
        ...submit handler...
        ui.button("Join waitlist", ...).props("color=primary").classes("q-mt-md")
    footer()
```

Replace layout portion with (all logic inside the card, inputs and button unchanged):
```python
    header()
    with ui.element("div").classes("th-page-wrap"):
        ui.html('<p class="th-label" style="margin-bottom: var(--space-4);">Private Beta</p>')
        with ui.element("div").classes("th-card"):
            ui.html(
                '<h2 class="th-card-title">Join the Waitlist</h2>'
                '<p class="th-card-body">Thinvite is currently in private beta. '
                'Enter your email and we\u2019ll notify you when access opens.</p>'
            )
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
                    f' data-size="invisible"></div>'
                )
            # submit_waitlist definition goes here (unchanged)
            ui.button("Join waitlist", on_click=submit_waitlist).classes(
                "th-btn-primary q-mt-md"
            ).props("no-caps unelevated")
    footer()
```

The `submit_waitlist` async function definition stays inside the `with ui.element("div").classes("th-card"):` block, defined before the button.

---

## Task 9: Rewrite contact_page()

**Files:**
- Modify: `web/main.py` — `contact_page()` function (~line 1104)

**Step 1: Same pattern as waitlist — preserve all logic, replace layout**

Replace layout portion with:
```python
    header()
    with ui.element("div").classes("th-page-wrap"):
        ui.html('<p class="th-label" style="margin-bottom: var(--space-4);">Get in Touch</p>')
        with ui.element("div").classes("th-card"):
            ui.html(
                '<h2 class="th-card-title">Contact Us</h2>'
                '<p class="th-card-body">Have a question or feedback? '
                'Send us a message.</p>'
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
            if _site_key:
                ui.html(
                    f'<div class="cf-turnstile"'
                    f' data-sitekey="{_site_key}"'
                    f' data-callback="onTurnstileSuccess"'
                    f' data-expired-callback="onTurnstileExpired"'
                    f' data-execution="render"'
                    f' data-size="invisible"></div>'
                )
            # submit_contact definition goes here (unchanged)
            ui.button("Send message", on_click=submit_contact).classes(
                "th-btn-primary q-mt-md"
            ).props("no-caps unelevated")
    footer()
```

---

## Task 10: Verify tests still pass

**Step 1: Run existing test suite**

```bash
cd /opt/websites/thinvite_sourk9_com && python -m pytest web/tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass (brand changes are visual only; no logic was modified).

**Step 2: Spot-check app syntax**

```bash
cd /opt/websites/thinvite_sourk9_com && python -c "import ast, pathlib; ast.parse(pathlib.Path('web/main.py').read_text()); print('syntax OK')"
```

Expected: `syntax OK`

---

## Task 11: Commit

```bash
cd /opt/websites/thinvite_sourk9_com
git add web/static/css/brand-tokens.css web/static/css/brand.css web/main.py
git commit -m "$(cat <<'EOF'
apply brand identity: fonts, colours, and public page redesigns

- Add brand-tokens.css and brand.css to static/css
- Wire Quasar --q-primary to brand violet; override header/footer bg
- Load Google Fonts (Syne + DM Sans) globally via shared head injection
- Update CSP to permit fonts.googleapis.com / fonts.gstatic.com
- Redesign header with SVG lockup and gradient wordmark accent
- Redesign footer with brand surface colours
- Redesign /, /redeem, /waitlist, /contact with brand hero/card layout
- /begin and /privacy inherit token-level styling automatically
EOF
)"
```
