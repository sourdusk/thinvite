# Brand Application Design
**Date:** 2026-03-06
**Scope:** Apply brand-guidelines.html + brand-tokens.css to the Thinvite site (Approach C — hybrid CSS globals + strategic ui.html())

## Goals
- Public-facing pages (`/`, `/redeem`, `/waitlist`, `/contact`) fully redesigned to match brand aesthetic
- Functional pages (`/begin`, `/privacy`) receive token-level styling only (fonts, colors)
- All interactive elements remain NiceGUI widgets (forms, buttons, dialogs)
- `ui.html()` used only for non-interactive visual structure (hero sections, cards, header lockup)

---

## 1. CSS Architecture

### Files
| File | Purpose |
|------|---------|
| `web/static/css/brand-tokens.css` | Copy of `docs/brand-tokens.css` — CSS custom properties only |
| `web/static/css/brand.css` | Quasar overrides + reusable brand classes |

### Global head injections (shared=True, in main.py)
1. Google Fonts link: Syne (600/700/800) + DM Sans (300/400/500/600 + italic)
2. `<link rel="stylesheet" href="/static/css/brand-tokens.css">`
3. `<link rel="stylesheet" href="/static/css/brand.css">`

### CSP additions
- `style-src`: add `https://fonts.googleapis.com`
- `font-src`: add `https://fonts.gstatic.com`

### brand.css contents
- Body base: font-family, background, color from tokens
- `--q-primary: #7c5af0` — wires brand violet into all NiceGUI `color="primary"` buttons
- `.q-header`, `.q-footer`: background `var(--color-surface-2)`, border-bottom/top
- Quasar typography overrides: `.text-h3`, `.text-h4`, `.text-h5`, `.text-h6` → Syne font-family
- Quasar input overrides: dark surface, border color
- Reusable brand classes: `.th-hero`, `.th-card`, `.th-label`, `.th-btn-primary`, `.th-btn-secondary`, `.th-badge`, `.th-grad-text`, `.th-section-label`, `.th-wordmark`

---

## 2. Header & Footer

### Header
- Container: `ui.header()` with `var(--color-surface-2)` background + bottom border
- Left: `ui.html()` with brand SVG lockup (icon + "Thin**vite**" in Syne 700, "vite" with gradient clip)
- Right: NiceGUI Logout button (when `show_logout=True`), styled flat white

### Footer
- Container: `ui.footer()` with `var(--color-surface)` background + top border
- Cookie notice: same logic, updated text/button colors to brand
- Links: `var(--color-muted)` text
- Copyright: DM Sans, muted

---

## 3. Home Page (`/`)

### Hero section — `ui.html()` block
- Full-width, centered column, `min-height: 80vh`
- Display text (DM Sans 300, 40px, `var(--color-frost)`):
  `"Twitch redeems → Discord access.` *`Securely.`*`"`
  — italic word rendered with gradient clip
- Subheading (Syne 700, 24px): `"Your Twitch. Your Discord."`
- Body (DM Sans 400, 15px, `var(--color-body-text)`):
  `"Streamers set up a channel point redeem. Viewers sign in with Twitch and receive a unique, single-use invite link. No sharing, no abuse."`
- Label chip (11px, uppercase, letter-spaced, violet-lt):
  `"Channel Redeems · Discord Invites · Verified Access"`

### CTA buttons — NiceGUI widgets
- "I'm a streamer" → primary gradient button
- "Redeem an invite" → secondary outline button

---

## 4. Redeem Page (`/redeem`)

### Structure
- Centered column card (`var(--color-surface-2)`, `border-radius: var(--radius-lg)`, border)
- `ui.html()` for the card shell + heading + body text
- NiceGUI Twitch login button inside, styled with brand radius + Twitch purple (#6441a5)
- Error notification unchanged (NiceGUI notify)

### Copy
- Heading (Syne 700, 24px): "Claim your Discord invite"
- Body: "Sign in with Twitch to verify your identity and claim your one-time invite link."
- Label badge: "Viewer Login"

---

## 5. Waitlist Page (`/waitlist`)

### Structure
- Same card shell as `/redeem`
- `ui.html()` for heading + descriptor
- NiceGUI inputs + submit button remain unchanged (functional)
- Heading: "Join the Waitlist"
- Body: "Thinvite is currently in private beta. Enter your email and we'll notify you when access opens."

---

## 6. Contact Page (`/contact`)

### Structure
- Same card shell
- Heading: "Contact Us"
- Body: "Have a question or feedback? Send us a message."
- All form elements remain NiceGUI

---

## 7. Functional Pages (token-level only)

### `/begin`
- Header/footer inherit brand automatically
- Quasar typography overrides give Syne headings
- Button colors already wired via `--q-primary`
- No layout or copy changes

### `/privacy`
- Header/footer inherit brand automatically
- Typography overrides apply
- No layout or copy changes

---

## Logo SVG (for reuse in header)
```svg
<svg width="32" height="32" viewBox="0 0 56 56" fill="none">
  <defs><linearGradient id="hdr" x1="0" y1="0" x2="56" y2="56" gradientUnits="userSpaceOnUse">
    <stop offset="0%" stop-color="#7c5af0"/><stop offset="100%" stop-color="#3d6ef5"/>
  </linearGradient></defs>
  <rect width="56" height="56" rx="14" fill="url(#hdr)"/>
  <circle cx="17.75" cy="36.25" r="10" stroke="white" stroke-width="2.5" fill="none"/>
  <circle cx="41.75" cy="16.25" r="6.5" fill="white"/>
  <line x1="28.25" y1="27.25" x2="35.75" y2="21.75" stroke="white" stroke-width="2.5" stroke-linecap="round"/>
</svg>
```

---

## Implementation Order
1. Copy brand-tokens.css → web/static/css/brand-tokens.css
2. Write web/static/css/brand.css
3. Update CSP in _SecurityHeadersMiddleware
4. Add global head injections to main.py
5. Rewrite header() and footer()
6. Rewrite home_page() (/)
7. Rewrite redeem_page() (/redeem)
8. Rewrite waitlist_page() (/waitlist)
9. Rewrite contact_page() (/contact)
