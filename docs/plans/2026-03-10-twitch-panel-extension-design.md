# Twitch Panel Extension Design

**Date:** 2026-03-10
**Status:** Draft

## Overview

A Twitch panel extension (318×496px) that lets viewers claim Discord invite links
directly from a streamer's channel page — no redirect to the Thinvite website needed.

Two invite paths, coexisting:

1. **Follow-age claims (new):** Viewers who have followed the channel for a
   configurable minimum duration can claim a Discord invite, subject to a
   per-viewer cooldown (e.g. one invite every 30 days).
2. **Channel-point redemption claims (existing, surfaced in-panel):** Viewers who
   redeemed channel points can claim their pending invite from the panel instead
   of visiting the website.

Both paths produce the same outcome: a single-use, 24-hour Discord invite link.

## Architecture

```
┌─────────────────────────────────────────┐
│  Twitch Channel Page                    │
│  ┌────────────────────┐                 │
│  │ Panel Extension    │  318×496px      │
│  │ (panel.html)       │                 │
│  │                    │                 │
│  │ Config cache ──────│── Twitch Configuration Service (no EBS call on load)
│  │                    │                 │
│  │ JWT ───────────────│──→ EBS: /api/ext/* routes (same Python app)
│  │                    │                 │
│  │ PubSub listener ←──│──← EBS whispers via Twitch Extension PubSub
│  └────────────────────┘                 │
└─────────────────────────────────────────┘
```

- **EBS** lives as new FastAPI routes in the existing `main.py` — reuses DB pool,
  `discorddb`, `twitch.py` helpers.
- **Auth** is Twitch Extension JWT (HS256, signed with shared secret). No OAuth
  needed for viewers.
- **PubSub** whispers notify a specific viewer's panel instantly when their
  channel-point redemption arrives (no polling).
- **Config** is set in the `/streamer` dashboard and synced to Twitch's
  Configuration Service so the panel can read cached settings without an EBS call
  on every load.

## EBS Endpoints

All endpoints require `Authorization: Bearer <jwt>` header. JWT is verified using
the extension shared secret (`TWITCH_EXT_SECRET` env var, base64-encoded).

Requests without a real `user_id` in the JWT (viewer hasn't shared identity) are
rejected with a clear error code so the panel can prompt identity sharing.

CORS: `Access-Control-Allow-Origin: https://extension-files.twitch.tv` on
`/api/ext/*` routes only. No CORS changes to existing routes.

### GET /api/ext/status

Returns the viewer's current state for this channel.

**Input:** JWT claims (`user_id`, `channel_id`)

**Logic:**
1. Look up streamer by `channel_id` (via `twitch_user_id`) → get config
2. Check `redemptions` table for pending channel-point redemption
3. Check `redemptions` table for any claim (any source) within cooldown period
4. If no pending and no recent claim → check follow age via Twitch Helix API
   using streamer's token (with retry-on-401 for token refresh)
5. Cache follow-age result in-memory for ~10 minutes per `channel_id:user_id`

**Response:**
```json
{
  "has_pending_redemption": false,
  "follow_age_eligible": true,
  "follow_age_days": 142,
  "cooldown_remaining_days": 0,
  "min_follow_days": 30
}
```

Error responses:
- `403 {"error": "identity_required"}` — viewer hasn't shared identity
- `404 {"error": "not_configured"}` — streamer hasn't set up extension
- `429` — rate limited

### POST /api/ext/claim

Creates a Discord invite and records the claim.

**Input:** JWT + `{"type": "redemption" | "follow_age"}`

**Logic (type=redemption):**
1. Find pending redemption for this viewer + streamer
2. Create Discord invite via `discorddb.create_invite()`
3. Mark redemption fulfilled in DB + fulfill on Twitch via Helix API
4. Return invite URL

**Logic (type=follow_age):**
1. Re-verify eligibility (don't trust client state):
   - Check unified cooldown across all sources in `redemptions`
   - Re-check follow age via Twitch API
2. Create Discord invite via `discorddb.create_invite()`
3. Insert row into `redemptions` with `source='follow_age'`
4. Return invite URL

**Response:** `{"invite_url": "https://discord.gg/xxx"}` or error

**Rate limit:** 5 req/60s per user.

### POST /api/ext/config (broadcaster only)

**Input:** JWT (must have `role: "broadcaster"`) +
`{"min_follow_days": 30, "cooldown_days": 30}`

**Logic:**
1. Validate inputs (positive integers, reasonable bounds)
2. Update `users` table (`ext_min_follow_days`, `ext_cooldown_days`)
3. Push config to Twitch Configuration Service (broadcaster segment) via Helix API

Also callable from the `/streamer` dashboard, which performs the same DB write +
Twitch Config Service push.

## DB Schema Changes

### Migration: `db/migrate_002.sql`

```sql
ALTER TABLE redemptions
    ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'channel_points';

ALTER TABLE users
    ADD COLUMN ext_min_follow_days INT DEFAULT NULL,
    ADD COLUMN ext_cooldown_days INT DEFAULT NULL;
```

The `source` column values: `'channel_points'`, `'follow_age'`, `'manual'`.

Existing rows default to `'channel_points'`. The `is_manual` boolean remains for
backward compatibility; new code writes both `is_manual=TRUE` and
`source='manual'` for manual invites.

### New DB functions (db.py)

- `get_ext_config(streamer_twitch_id) -> dict | None`
- `has_recent_invite(viewer_twitch_id, streamer_session_id, cooldown_days) -> bool`
  — single query on `redemptions` checking all sources within cooldown window
- `add_ext_claim(streamer_session_id, viewer_twitch_id, viewer_twitch_name, invite_url) -> int`
  — inserts into `redemptions` with `source='follow_age'`
- `set_ext_config(session_id, min_follow_days, cooldown_days) -> None`
- `get_user_by_twitch_id(twitch_user_id)` — already exists

## EventSub → PubSub Integration

When `_handle_eventsub_event` in `main.py` processes a new channel-point
redemption (after `add_redemption()` succeeds):

```python
# Fire-and-forget PubSub whisper to the viewer's extension panel
asyncio.create_task(
    ext_pubsub.send_whisper(
        channel_id=broadcaster_id,
        viewer_user_id=viewer_twitch_id,
        message={"type": "redemption_ready"}
    )
)
```

### Deduplication with follow-age claims

The EventSub handler gains an additional check: if the viewer has a recent invite
(any source) within the cooldown period, cancel the Twitch redemption (refunding
points) and send a chat message:
"@viewer You already have a pending/recent invite. Points refunded."

### New module: `web/ext_pubsub.py` (~40 lines)

- `send_whisper(channel_id, viewer_user_id, message)` — signs a JWT with the
  extension secret, POSTs to Twitch Extension PubSub endpoint
- Non-blocking, fire-and-forget with error logging

## Twitch API Changes

### New OAuth scope for streamers

Add `moderator:read:followers` to the streamer's Twitch OAuth scope. Existing
streamers will need to re-authorize to use the follow-age feature.

### New helper in `twitch.py`

- `get_follow_age(broadcaster_id, user_id, token) -> int | None` — calls
  `GET /helix/channels/followers?broadcaster_id=X&user_id=Y`, returns follow
  duration in days, or None if not following. Implements retry-on-401 (calls
  `refresh_auth_token()` on failure, updates DB, retries once).

### Follow-age caching

In-memory dict in the EBS: `_follow_age_cache[f"{channel_id}:{user_id}"]` =
`(days, timestamp)`. TTL: 10 minutes. Prevents hammering the Twitch API when
a viewer refreshes the panel.

## Extension Frontend

### Files

```
extension/
  panel.html       — viewer panel (318×496px)
  config.html      — broadcaster configuration page
  panel.css        — styles (light + dark theme)
  panel.js         — logic: auth, status check, claim, PubSub listener
  config.js        — config page logic
  config.css       — config page styles
```

Vanilla HTML/CSS/JS. No framework — the panel is tiny and must load fast.

### Panel States

| State | Display |
|---|---|
| Loading | Spinner while `onAuthorized` fires |
| Identity required | Prompt to share identity with explanation |
| Not configured | "Streamer hasn't set up Discord invites yet" |
| Eligible (follow-age) | Follow duration + [Claim Invite] button |
| Pending redemption | "You redeemed channel points!" + [Claim Invite] button |
| Both available | Show both options, viewer picks one |
| Cooldown active | "Claimed X days ago, available again in Y days" |
| Not eligible | "Follow for X more days to earn a Discord invite" |
| Success | Discord invite link + [Join Server] button |
| Error | Contextual message + retry option |

### Theming

Reads `Twitch.ext.onContext()` for `theme` property (`"light"` or `"dark"`).
Applies Twitch-recommended colors:
- Light: bg `#fff`, text `#232127`
- Dark: bg `#201c2b`, text `#e5e3e8`

### Config Page (config.html)

Simple form: minimum follow days (number input) + cooldown days (number input) +
Save button. Calls `POST /api/ext/config` with broadcaster JWT. Shows current
saved values on load (read from Twitch Configuration Service cache). Also
displays: "You can also configure these settings from your Thinvite dashboard."

## Environment Variables (new)

- `TWITCH_EXT_SECRET` — Extension shared secret (base64-encoded), from Twitch
  developer console. Used for JWT verification and signing PubSub JWTs.
- `TWITCH_EXT_CLIENT_ID` — Extension client ID (for PubSub API calls).
- `TWITCH_EXT_OWNER_ID` — Twitch user ID of the extension owner (for signing
  EBS JWTs with `user_id` claim).

## Security Considerations

- **JWT verification on every request** — HS256 with shared secret, check `exp`
- **Re-verify eligibility on claim** — never trust client-side state
- **CORS scoped to extension origin only** — `extension-files.twitch.tv`
- **Rate limiting** — 5 req/60s per user on claim endpoint
- **No session cookies** — entirely stateless JWT auth on ext endpoints
- **Follow-age cache is server-side only** — clients can't poison it
- **PubSub failures are non-blocking** — never break the redemption flow

## Streamer Onboarding (extension is optional)

1. Set up Thinvite as usual (Twitch + Discord OAuth on `/streamer`)
2. Re-authorize Twitch with new `moderator:read:followers` scope (prompted in
   dashboard when extension settings are accessed)
3. Configure extension settings in `/streamer` dashboard (min follow days,
   cooldown)
4. Install the Twitch extension on their channel from the Twitch extension
   marketplace
5. Activate the panel on their channel page

Steps 2-5 are only needed if the streamer wants the panel extension. The existing
channel-point flow works without any of this.
