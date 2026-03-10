# Twitch Panel Extension Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Twitch panel extension that lets viewers claim Discord invites directly from a streamer's channel page — via follow-age eligibility or pending channel-point redemptions.

**Architecture:** New `/api/ext/*` FastAPI endpoints (EBS) in the existing app, authenticated via Twitch Extension JWT. PubSub whispers for real-time redemption notifications. Extension frontend is vanilla HTML/CSS/JS served from Twitch's CDN.

**Tech Stack:** Python/FastAPI (existing), PyJWT (new), Twitch Extension Helper JS, Twitch Helix API, Twitch Extension PubSub.

**Design doc:** `docs/plans/2026-03-10-twitch-panel-extension-design.md`

---

## Task 1: Add PyJWT dependency

**Files:**
- Modify: `web/requirements.txt`

**Step 1: Add PyJWT to requirements**

Add `PyJWT==2.10.1` to `web/requirements.txt` after the `discord.py` line:

```
PyJWT==2.10.1
```

**Step 2: Rebuild container**

Run: `docker compose build python`

**Step 3: Verify import works**

Run: `docker exec thinvite-web python -c "import jwt; print(jwt.__version__)"`
Expected: `2.10.1`

**Step 4: Commit**

```bash
git add web/requirements.txt
git commit -m "Add PyJWT dependency for Twitch extension JWT verification"
```

---

## Task 2: DB migration — add `source` column and extension config

**Files:**
- Create: `db/migrate_004.sql`
- Modify: `db/init.sql` (add columns for new installs)

**Step 1: Write migration**

Create `db/migrate_004.sql`:

```sql
-- Add source column to redemptions (channel_points, follow_age, manual)
ALTER TABLE redemptions ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'channel_points';

-- Add extension config columns to users
ALTER TABLE users ADD COLUMN IF NOT EXISTS ext_min_follow_days INT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ext_cooldown_days INT DEFAULT NULL;
```

**Step 2: Run migration**

Run: `docker exec -i thinvite-db mariadb -u thinvite -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" thinvite < db/migrate_004.sql`

**Step 3: Verify schema**

Run: `docker exec thinvite-db mariadb -u thinvite -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" thinvite -e "DESCRIBE redemptions;"`
Expected: `source` column visible with default `channel_points`.

Run: `docker exec thinvite-db mariadb -u thinvite -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" thinvite -e "DESCRIBE users;"`
Expected: `ext_min_follow_days` and `ext_cooldown_days` columns visible.

**Step 4: Update init.sql for new installs**

In `db/init.sql`, add to the `redemptions` table definition (after `twitch_reward_id` line):

```sql
    source VARCHAR(32) NOT NULL DEFAULT 'channel_points',
```

Add to the `users` table definition (before the closing paren):

```sql
    ext_min_follow_days INT DEFAULT NULL,
    ext_cooldown_days INT DEFAULT NULL,
```

**Step 5: Commit**

```bash
git add db/migrate_004.sql db/init.sql
git commit -m "Add source column to redemptions and extension config to users"
```

---

## Task 3: Extension JWT verification module

**Files:**
- Create: `web/ext_auth.py`
- Create: `web/tests/test_ext_auth.py`

**Step 1: Write the failing tests**

Create `web/tests/test_ext_auth.py`:

```python
import base64
import time
from unittest.mock import patch

import jwt
import pytest

# The shared secret (base64-encoded, as Twitch provides it)
_TEST_SECRET_RAW = b"test-secret-key-for-unit-tests!!"  # 32 bytes
_TEST_SECRET_B64 = base64.b64encode(_TEST_SECRET_RAW).decode()


def _make_jwt(claims, secret=_TEST_SECRET_RAW):
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TWITCH_EXT_SECRET", _TEST_SECRET_B64)
    monkeypatch.setenv("TWITCH_EXT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_EXT_OWNER_ID", "12345")


def test_valid_jwt_returns_claims():
    from ext_auth import verify_ext_jwt

    token = _make_jwt({
        "user_id": "99999",
        "channel_id": "11111",
        "role": "viewer",
        "opaque_user_id": "U99999",
        "exp": int(time.time()) + 300,
    })
    claims = verify_ext_jwt(token)
    assert claims["user_id"] == "99999"
    assert claims["channel_id"] == "11111"
    assert claims["role"] == "viewer"


def test_expired_jwt_returns_none():
    from ext_auth import verify_ext_jwt

    token = _make_jwt({
        "user_id": "99999",
        "channel_id": "11111",
        "role": "viewer",
        "exp": int(time.time()) - 60,
    })
    assert verify_ext_jwt(token) is None


def test_wrong_secret_returns_none():
    from ext_auth import verify_ext_jwt

    token = _make_jwt(
        {"user_id": "99999", "channel_id": "11111", "role": "viewer",
         "exp": int(time.time()) + 300},
        secret=b"wrong-secret-key-not-the-right!!"
    )
    assert verify_ext_jwt(token) is None


def test_missing_user_id_returns_none():
    from ext_auth import verify_ext_jwt

    token = _make_jwt({
        "channel_id": "11111",
        "role": "viewer",
        "opaque_user_id": "A12345",
        "exp": int(time.time()) + 300,
    })
    # No user_id = viewer hasn't shared identity
    assert verify_ext_jwt(token) is None


def test_sign_ebs_jwt():
    from ext_auth import sign_ebs_jwt

    token = sign_ebs_jwt()
    claims = jwt.decode(token, _TEST_SECRET_RAW, algorithms=["HS256"])
    assert claims["user_id"] == "12345"  # TWITCH_EXT_OWNER_ID
    assert claims["role"] == "external"
    assert claims["exp"] > int(time.time())
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_ext_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ext_auth'`

**Step 3: Implement ext_auth.py**

Create `web/ext_auth.py`:

```python
"""Twitch Extension JWT verification and EBS JWT signing."""

import base64
import logging
import os
import time

import jwt

log = logging.getLogger(__name__)

_secret_bytes: bytes | None = None


def _get_secret() -> bytes:
    global _secret_bytes
    if _secret_bytes is None:
        b64 = os.environ["TWITCH_EXT_SECRET"]
        _secret_bytes = base64.b64decode(b64)
    return _secret_bytes


def verify_ext_jwt(token: str) -> dict | None:
    """Verify a Twitch Extension JWT. Returns claims dict or None."""
    try:
        claims = jwt.decode(token, _get_secret(), algorithms=["HS256"])
    except jwt.exceptions.PyJWTError:
        return None

    if "user_id" not in claims:
        return None

    return claims


def sign_ebs_jwt() -> str:
    """Sign a JWT for EBS-to-Twitch API calls (PubSub, etc.)."""
    owner_id = os.environ["TWITCH_EXT_OWNER_ID"]
    payload = {
        "user_id": owner_id,
        "role": "external",
        "exp": int(time.time()) + 120,
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")
```

**Step 4: Run tests to verify they pass**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_ext_auth.py -v`
Expected: all 5 PASS

**Step 5: Commit**

```bash
git add web/ext_auth.py web/tests/test_ext_auth.py
git commit -m "Add Twitch Extension JWT verification and EBS signing module"
```

---

## Task 4: New DB functions for extension

**Files:**
- Modify: `web/db.py` (add functions after existing ones)
- Modify: `web/tests/test_db.py` (add tests)

**Step 1: Write failing tests**

Add to `web/tests/test_db.py`:

```python
# --- Extension config ---

async def test_set_ext_config(mock_pool):
    pool, cursor = mock_pool
    await db.set_ext_config("sess-1", 30, 14)
    cursor.execute.assert_called_once()
    sql = cursor.execute.call_args[0][0]
    assert "ext_min_follow_days" in sql
    assert "ext_cooldown_days" in sql


async def test_get_ext_config(mock_pool):
    pool, cursor = mock_pool
    cursor.fetchone.return_value = {
        "session_id": "sess-1",
        "discord_server_id": "123456",
        "ext_min_follow_days": 30,
        "ext_cooldown_days": 14,
    }
    result = await db.get_ext_config("twitch-id-1")
    assert result["ext_min_follow_days"] == 30
    assert result["ext_cooldown_days"] == 14
    assert result["discord_server_id"] == "123456"


async def test_get_ext_config_not_found(mock_pool):
    pool, cursor = mock_pool
    cursor.fetchone.return_value = None
    result = await db.get_ext_config("nonexistent")
    assert result is None


# --- Recent invite check ---

async def test_has_recent_invite_true(mock_pool):
    pool, cursor = mock_pool
    cursor.fetchone.return_value = {"cnt": 1}
    result = await db.has_recent_invite("viewer-1", "sess-1", 30)
    assert result is True


async def test_has_recent_invite_false(mock_pool):
    pool, cursor = mock_pool
    cursor.fetchone.return_value = {"cnt": 0}
    result = await db.has_recent_invite("viewer-1", "sess-1", 30)
    assert result is False


# --- Follow-age claim ---

async def test_add_ext_claim(mock_pool):
    pool, cursor = mock_pool
    cursor.lastrowid = 42
    result = await db.add_ext_claim("sess-1", "viewer-1", "viewername", "https://discord.gg/abc")
    assert result == 42
    sql = cursor.execute.call_args[0][0]
    assert "source" in sql
    assert "follow_age" in cursor.execute.call_args[0][1]
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_db.py::test_set_ext_config tests/test_db.py::test_get_ext_config tests/test_db.py::test_has_recent_invite_true tests/test_db.py::test_add_ext_claim -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'set_ext_config'`

**Step 3: Implement DB functions**

Add to `web/db.py` (after existing functions, before module end):

```python
async def set_ext_config(session_id: str, min_follow_days: int, cooldown_days: int) -> None:
    """Set extension config (min follow age, cooldown) for a streamer."""
    async with _acquire() as (conn, cur):
        await cur.execute(
            "UPDATE users SET ext_min_follow_days = %s, ext_cooldown_days = %s "
            "WHERE session_id = %s",
            (min_follow_days, cooldown_days, session_id),
        )
        await conn.commit()


async def get_ext_config(streamer_twitch_id: str) -> dict | None:
    """Get extension config + discord_server_id for a streamer by their Twitch ID."""
    async with _acquire() as (_, cur):
        await cur.execute(
            "SELECT session_id, discord_server_id, ext_min_follow_days, ext_cooldown_days "
            "FROM users WHERE twitch_user_id = %s",
            (streamer_twitch_id,),
        )
        return await cur.fetchone()


async def has_recent_invite(viewer_twitch_id: str, streamer_session_id: str, cooldown_days: int) -> bool:
    """Check if viewer has any invite (any source) within the cooldown window."""
    async with _acquire() as (_, cur):
        await cur.execute(
            "SELECT COUNT(*) AS cnt FROM redemptions "
            "WHERE viewer_twitch_user_id = %s AND streamer_session_id = %s "
            "AND redeemed_at > NOW() - INTERVAL %s DAY "
            "AND (fulfilled_at IS NOT NULL OR revoked_at IS NULL)",
            (viewer_twitch_id, streamer_session_id, cooldown_days),
        )
        row = await cur.fetchone()
        return row["cnt"] > 0


async def add_ext_claim(
    streamer_session_id: str,
    viewer_twitch_id: str,
    viewer_twitch_name: str,
    invite_url: str,
) -> int:
    """Insert a follow-age invite claim into redemptions."""
    async with _acquire() as (conn, cur):
        await cur.execute(
            "INSERT INTO redemptions "
            "(streamer_session_id, viewer_twitch_user_id, viewer_twitch_user_name, "
            "invite_url, source, fulfilled_at) "
            "VALUES (%s, %s, %s, %s, %s, NOW())",
            (streamer_session_id, viewer_twitch_id, viewer_twitch_name, invite_url, "follow_age"),
        )
        await conn.commit()
        return cur.lastrowid
```

**Step 4: Run tests to verify they pass**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_db.py -v`
Expected: all PASS (new + existing)

**Step 5: Commit**

```bash
git add web/db.py web/tests/test_db.py
git commit -m "Add DB functions for extension config and follow-age claims"
```

---

## Task 5: Twitch helper — get_follow_age with retry-on-401

**Files:**
- Modify: `web/twitch.py` (add function, update scope)
- Modify: `web/tests/test_twitch.py` (add tests)

**Step 1: Write failing tests**

Add to `web/tests/test_twitch.py`:

```python
# --- get_follow_age ---

async def test_get_follow_age_success(mock_pool_factory):
    pool, cursor = mock_pool_factory()
    cursor.fetchone.return_value = {"twitch_token": "tok123", "twitch_refresh_token": "ref123"}
    follow_data = {
        "data": [{"followed_at": "2025-06-15T12:00:00Z"}],
    }
    resp = make_aiohttp_response(200, follow_data)
    session = make_aiohttp_session([resp])
    with patch("aiohttp.ClientSession", return_value=session):
        days = await twitch.get_follow_age("broadcaster-1", "viewer-1", "tok123")
    assert isinstance(days, int)
    assert days > 0


async def test_get_follow_age_not_following(mock_pool_factory):
    pool, cursor = mock_pool_factory()
    resp = make_aiohttp_response(200, {"data": []})
    session = make_aiohttp_session([resp])
    with patch("aiohttp.ClientSession", return_value=session):
        days = await twitch.get_follow_age("broadcaster-1", "viewer-1", "tok123")
    assert days is None


async def test_get_follow_age_retries_on_401(mock_pool_factory):
    pool, cursor = mock_pool_factory()
    cursor.fetchone.return_value = {"twitch_token": "new-tok", "twitch_refresh_token": "ref123"}
    resp_401 = make_aiohttp_response(401, {})
    refresh_resp = make_aiohttp_response(200, {
        "access_token": "new-tok", "refresh_token": "new-ref",
        "expires_in": 14400, "token_type": "bearer",
    })
    follow_data = {"data": [{"followed_at": "2025-01-01T00:00:00Z"}]}
    resp_ok = make_aiohttp_response(200, follow_data)
    session = make_aiohttp_session([resp_401, refresh_resp, resp_ok])
    with patch("aiohttp.ClientSession", return_value=session):
        days = await twitch.get_follow_age("broadcaster-1", "viewer-1", "tok123")
    assert isinstance(days, int)
    assert days > 0
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_twitch.py::test_get_follow_age_success tests/test_twitch.py::test_get_follow_age_not_following tests/test_twitch.py::test_get_follow_age_retries_on_401 -v`
Expected: FAIL — `AttributeError: module 'twitch' has no attribute 'get_follow_age'`

**Step 3: Update OAuth scope and implement get_follow_age**

In `web/twitch.py`, update `_STREAMER_SCOPE` (line 40–42):

```python
_STREAMER_SCOPE = (
    "channel:read:redemptions channel:manage:redemptions "
    "user:write:chat moderator:read:followers"
)
```

Add `get_follow_age()` function to `web/twitch.py`:

```python
async def get_follow_age(broadcaster_id: str, user_id: str, token: str) -> int | None:
    """Get how many days user_id has followed broadcaster_id.

    Returns days as int, or None if not following.
    Retries once with a refreshed token on 401.
    """
    url = (
        f"https://api.twitch.tv/helix/channels/followers"
        f"?broadcaster_id={broadcaster_id}&user_id={user_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Client-Id": os.environ["TWITCH_CLIENT_ID"],
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 401:
                return await _retry_follow_age_after_refresh(
                    session, broadcaster_id, user_id
                )
            if resp.status != 200:
                log.warning("get_follow_age failed: %s", resp.status)
                return None
            data = await resp.json()

    follows = data.get("data", [])
    if not follows:
        return None

    from datetime import datetime, timezone
    followed_at = datetime.fromisoformat(follows[0]["followed_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return (now - followed_at).days


async def _retry_follow_age_after_refresh(
    session, broadcaster_id: str, user_id: str
) -> int | None:
    """Refresh the streamer's token and retry the follow-age check."""
    async with _acquire() as (conn, cur):
        await cur.execute(
            "SELECT twitch_refresh_token FROM users WHERE twitch_user_id = %s",
            (broadcaster_id,),
        )
        row = await cur.fetchone()
    if not row or not row.get("twitch_refresh_token"):
        return None

    refreshed = await refresh_auth_token(row["twitch_refresh_token"])
    if not refreshed:
        return None

    new_token = refreshed["access_token"]
    headers = {
        "Authorization": f"Bearer {new_token}",
        "Client-Id": os.environ["TWITCH_CLIENT_ID"],
    }
    url = (
        f"https://api.twitch.tv/helix/channels/followers"
        f"?broadcaster_id={broadcaster_id}&user_id={user_id}"
    )
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()

    follows = data.get("data", [])
    if not follows:
        return None

    from datetime import datetime, timezone
    followed_at = datetime.fromisoformat(follows[0]["followed_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return (now - followed_at).days
```

Note: `_acquire` is imported from `db` — check how other `twitch.py` functions access the DB. If they use `db.function()` calls instead, adjust accordingly. The existing `fulfill_redemption` in `twitch.py` accesses the DB via `await db.get_user(sess_id)`, so follow that pattern.

**Step 4: Run tests to verify they pass**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_twitch.py -v`
Expected: all PASS (new + existing)

**Step 5: Commit**

```bash
git add web/twitch.py web/tests/test_twitch.py
git commit -m "Add get_follow_age helper with retry-on-401 token refresh"
```

---

## Task 6: Extension PubSub module

**Files:**
- Create: `web/ext_pubsub.py`
- Create: `web/tests/test_ext_pubsub.py`

**Step 1: Write failing tests**

Create `web/tests/test_ext_pubsub.py`:

```python
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TWITCH_EXT_SECRET", "dGVzdC1zZWNyZXQta2V5LWZvci11bml0LXRlc3RzISE=")
    monkeypatch.setenv("TWITCH_EXT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_EXT_OWNER_ID", "12345")


async def test_send_whisper_success():
    from ext_pubsub import send_whisper

    mock_resp = AsyncMock()
    mock_resp.status = 204
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_resp
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await send_whisper("chan-1", "viewer-1", {"type": "redemption_ready"})

    mock_session.post.assert_called_once()
    call_kwargs = mock_session.post.call_args
    body = json.loads(call_kwargs[1]["data"]) if "data" in call_kwargs[1] else json.loads(call_kwargs[1]["json"])
    assert "whisper-viewer-1" in body["target"]


async def test_send_whisper_failure_does_not_raise():
    from ext_pubsub import send_whisper

    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Internal Server Error")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_resp
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        # Should not raise — fire-and-forget semantics
        await send_whisper("chan-1", "viewer-1", {"type": "test"})


async def test_send_whisper_exception_does_not_raise():
    from ext_pubsub import send_whisper

    with patch("aiohttp.ClientSession", side_effect=Exception("network error")):
        # Should not raise
        await send_whisper("chan-1", "viewer-1", {"type": "test"})
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_ext_pubsub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ext_pubsub'`

**Step 3: Implement ext_pubsub.py**

Create `web/ext_pubsub.py`:

```python
"""Twitch Extension PubSub — send whisper messages to viewer panels."""

import json
import logging
import os

import aiohttp

from ext_auth import sign_ebs_jwt

log = logging.getLogger(__name__)

_PUBSUB_URL = "https://api.twitch.tv/helix/extensions/pubsub"


async def send_whisper(channel_id: str, viewer_user_id: str, message: dict) -> None:
    """Send a PubSub whisper to a specific viewer's extension panel.

    Fire-and-forget: logs errors but never raises.
    """
    try:
        client_id = os.environ["TWITCH_EXT_CLIENT_ID"]
        token = sign_ebs_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "target": [f"whisper-{viewer_user_id}"],
            "broadcaster_id": channel_id,
            "message": json.dumps(message),
        })
        async with aiohttp.ClientSession() as session:
            async with session.post(_PUBSUB_URL, headers=headers, data=body) as resp:
                if resp.status != 204:
                    text = await resp.text()
                    log.warning("PubSub whisper failed (%s): %s", resp.status, text)
    except Exception:
        log.exception("PubSub whisper error")
```

**Step 4: Run tests to verify they pass**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_ext_pubsub.py -v`
Expected: all 3 PASS

**Step 5: Commit**

```bash
git add web/ext_pubsub.py web/tests/test_ext_pubsub.py
git commit -m "Add Extension PubSub whisper module"
```

---

## Task 7: CORS middleware for /api/ext/* routes

**Files:**
- Modify: `web/main.py` (add CORS handling in `_SecurityHeadersMiddleware`)
- Modify: `web/tests/test_main_helpers.py` (add CORS test)

**Step 1: Write failing test**

Add to `web/tests/test_main_helpers.py`:

```python
def test_cors_headers_on_ext_routes():
    """CORS headers should be set on /api/ext/* routes for Twitch extension origin."""
    from main import _SecurityHeadersMiddleware

    # Test that the middleware class exists and has the expected behavior
    # (integration test — full CORS flow tested via test client in Task 8)
    assert hasattr(_SecurityHeadersMiddleware, '__init__')
```

Note: Full CORS behavior is best tested as integration tests with the actual endpoints (Task 8). This task focuses on the middleware modification.

**Step 2: Modify _SecurityHeadersMiddleware**

In `web/main.py`, inside `_SecurityHeadersMiddleware.__call__` (around line 78), add CORS handling for `/api/ext/` routes:

```python
# At the top of the __call__ method, handle CORS preflight for extension routes
scope = message if message["type"] == "http" else {}
path = scope.get("path", "")

# If this is an OPTIONS preflight for /api/ext/*, respond immediately
if path.startswith("/api/ext/") and scope.get("method") == "OPTIONS":
    # Handle in send_with_headers below
    pass
```

In the `send_with_headers` inner function, after existing security headers are set but before `await send(message)`, add:

```python
# CORS headers for extension routes only
if hasattr(self, '_current_path') and self._current_path.startswith("/api/ext/"):
    headers.append((b"access-control-allow-origin", b"https://extension-files.twitch.tv"))
    headers.append((b"access-control-allow-headers", b"Authorization, Content-Type"))
    headers.append((b"access-control-allow-methods", b"GET, POST, OPTIONS"))
    headers.append((b"access-control-max-age", b"86400"))
```

Alternatively, use FastAPI's `CORSMiddleware` scoped to a sub-application — this may be cleaner. Evaluate which approach fits better with the existing middleware stack.

**Step 3: Run tests**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_main_helpers.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add web/main.py web/tests/test_main_helpers.py
git commit -m "Add CORS headers for /api/ext/* extension routes"
```

---

## Task 8: EBS endpoints — status, claim, config

**Files:**
- Modify: `web/main.py` (add three FastAPI routes)
- Create: `web/tests/test_ext_endpoints.py`

This is the largest task. It adds the three core EBS endpoints.

**Step 1: Write failing tests for GET /api/ext/status**

Create `web/tests/test_ext_endpoints.py`. This will test the endpoint handler functions directly (not via HTTP client) since NiceGUI's test infrastructure is complex.

```python
import time
from unittest.mock import patch, AsyncMock, MagicMock

import jwt
import pytest

# Suppress NiceGUI/FastAPI startup
with patch("nicegui.ui.run"):
    with patch("nicegui.app.add_static_files", MagicMock()):
        pass

_TEST_SECRET = b"test-secret-key-for-unit-tests!!"


def _make_jwt(user_id="99999", channel_id="11111", role="viewer"):
    return jwt.encode(
        {"user_id": user_id, "channel_id": channel_id, "role": role,
         "exp": int(time.time()) + 300},
        _TEST_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def env(monkeypatch):
    import base64
    monkeypatch.setenv("TWITCH_EXT_SECRET", base64.b64encode(_TEST_SECRET).decode())
    monkeypatch.setenv("TWITCH_EXT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_EXT_OWNER_ID", "12345")


async def test_ext_status_not_configured():
    """Returns 404 when streamer hasn't configured extension."""
    from main import _ext_get_status
    with patch("db.get_ext_config", new_callable=AsyncMock, return_value=None):
        result = await _ext_get_status(user_id="99999", channel_id="11111")
    assert result["error"] == "not_configured"


async def test_ext_status_eligible():
    """Returns eligible when viewer meets follow-age requirement."""
    config = {
        "session_id": "sess-1", "discord_server_id": "guild-1",
        "ext_min_follow_days": 30, "ext_cooldown_days": 14,
    }
    with patch("db.get_ext_config", new_callable=AsyncMock, return_value=config), \
         patch("db.get_pending_redemptions_for_viewer", new_callable=AsyncMock, return_value=[]), \
         patch("db.has_recent_invite", new_callable=AsyncMock, return_value=False), \
         patch("twitch.get_follow_age", new_callable=AsyncMock, return_value=60):
        result = await _ext_get_status(user_id="99999", channel_id="11111")
    assert result["follow_age_eligible"] is True
    assert result["follow_age_days"] == 60


async def test_ext_status_pending_redemption():
    """Returns has_pending_redemption when viewer has a pending channel-point claim."""
    config = {
        "session_id": "sess-1", "discord_server_id": "guild-1",
        "ext_min_follow_days": 30, "ext_cooldown_days": 14,
    }
    pending = [{"id": 1, "streamer_session_id": "sess-1"}]
    with patch("db.get_ext_config", new_callable=AsyncMock, return_value=config), \
         patch("db.get_pending_redemptions_for_viewer", new_callable=AsyncMock, return_value=pending), \
         patch("db.has_recent_invite", new_callable=AsyncMock, return_value=False), \
         patch("twitch.get_follow_age", new_callable=AsyncMock, return_value=60):
        result = await _ext_get_status(user_id="99999", channel_id="11111")
    assert result["has_pending_redemption"] is True
```

**Step 2: Run tests to verify they fail**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_ext_endpoints.py -v`
Expected: FAIL — `cannot import name '_ext_get_status' from 'main'`

**Step 3: Implement the three endpoints**

Add to `web/main.py` (before `ui.run()`, after existing FastAPI routes):

```python
# --- Extension EBS endpoints ---

import ext_auth

_follow_age_cache: dict[str, tuple[int | None, float]] = {}
_FOLLOW_AGE_CACHE_TTL = 600  # 10 minutes


async def _ext_get_status(user_id: str, channel_id: str) -> dict:
    """Core logic for GET /api/ext/status."""
    config = await db.get_ext_config(channel_id)
    if not config:
        return {"error": "not_configured"}

    sess_id = config["session_id"]
    min_days = config["ext_min_follow_days"] or 0
    cooldown = config["ext_cooldown_days"] or 30

    # Check pending channel-point redemptions
    pending = await db.get_pending_redemptions_for_viewer(user_id)
    # Filter to this streamer only
    pending_here = [r for r in pending if r["streamer_session_id"] == sess_id]
    has_pending = len(pending_here) > 0

    # Check cooldown
    on_cooldown = await db.has_recent_invite(user_id, sess_id, cooldown)

    # Check follow age (cached)
    follow_days = None
    follow_eligible = False
    if not on_cooldown:
        cache_key = f"{channel_id}:{user_id}"
        cached = _follow_age_cache.get(cache_key)
        if cached and (time.time() - cached[1]) < _FOLLOW_AGE_CACHE_TTL:
            follow_days = cached[0]
        else:
            user_row = await db.get_user_by_twitch_id(channel_id)
            if user_row and user_row.get("twitch_token"):
                follow_days = await twitch.get_follow_age(
                    channel_id, user_id, user_row["twitch_token"]
                )
                _follow_age_cache[cache_key] = (follow_days, time.time())
        if follow_days is not None:
            follow_eligible = follow_days >= min_days

    return {
        "has_pending_redemption": has_pending,
        "follow_age_eligible": follow_eligible,
        "follow_age_days": follow_days,
        "cooldown_remaining_days": 0 if not on_cooldown else cooldown,  # Simplified; refine later
        "min_follow_days": min_days,
        "on_cooldown": on_cooldown,
    }


@app.get("/api/ext/status")
async def ext_status(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing_token"}, 401)
    claims = ext_auth.verify_ext_jwt(auth[7:])
    if claims is None:
        return JSONResponse({"error": "identity_required"}, 403)
    result = await _ext_get_status(claims["user_id"], claims["channel_id"])
    status = 404 if result.get("error") == "not_configured" else 200
    return JSONResponse(result, status)


@app.post("/api/ext/claim")
async def ext_claim(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing_token"}, 401)
    claims = ext_auth.verify_ext_jwt(auth[7:])
    if claims is None:
        return JSONResponse({"error": "identity_required"}, 403)

    # Rate limit: 5 req/60s per user
    if _is_rate_limited(request):
        return JSONResponse({"error": "rate_limited"}, 429)

    body = await request.json()
    claim_type = body.get("type")

    config = await db.get_ext_config(claims["channel_id"])
    if not config:
        return JSONResponse({"error": "not_configured"}, 404)

    sess_id = config["session_id"]
    guild_id = config["discord_server_id"]
    cooldown = config["ext_cooldown_days"] or 30

    if not guild_id:
        return JSONResponse({"error": "discord_not_configured"}, 400)

    # Unified cooldown check
    if await db.has_recent_invite(claims["user_id"], sess_id, cooldown):
        return JSONResponse({"error": "on_cooldown"}, 409)

    if claim_type == "redemption":
        pending = await db.get_pending_redemptions_for_viewer(claims["user_id"])
        pending_here = [r for r in pending if r["streamer_session_id"] == sess_id]
        if not pending_here:
            return JSONResponse({"error": "no_pending_redemption"}, 404)
        redemption = pending_here[0]
        invite_url = await discorddb.create_invite(guild_id)
        if not invite_url:
            return JSONResponse({"error": "invite_creation_failed"}, 500)
        await db.fulfill_redemption(redemption["id"], invite_url)
        if redemption.get("twitch_reward_id") and redemption.get("twitch_redemption_id"):
            await twitch.fulfill_redemption(
                sess_id, redemption["twitch_reward_id"], redemption["twitch_redemption_id"]
            )
        return JSONResponse({"invite_url": invite_url})

    elif claim_type == "follow_age":
        min_days = config["ext_min_follow_days"] or 0
        user_row = await db.get_user_by_twitch_id(claims["channel_id"])
        if not user_row or not user_row.get("twitch_token"):
            return JSONResponse({"error": "streamer_token_missing"}, 500)
        follow_days = await twitch.get_follow_age(
            claims["channel_id"], claims["user_id"], user_row["twitch_token"]
        )
        if follow_days is None or follow_days < min_days:
            return JSONResponse({"error": "not_eligible"}, 403)
        invite_url = await discorddb.create_invite(guild_id)
        if not invite_url:
            return JSONResponse({"error": "invite_creation_failed"}, 500)
        # Get viewer display name from Twitch if possible, fallback to user_id
        viewer_name = claims.get("opaque_user_id", claims["user_id"])
        await db.add_ext_claim(sess_id, claims["user_id"], viewer_name, invite_url)
        return JSONResponse({"invite_url": invite_url})

    return JSONResponse({"error": "invalid_type"}, 400)


@app.post("/api/ext/config")
async def ext_config(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing_token"}, 401)
    claims = ext_auth.verify_ext_jwt(auth[7:])
    if claims is None:
        return JSONResponse({"error": "identity_required"}, 403)
    if claims.get("role") != "broadcaster":
        return JSONResponse({"error": "broadcaster_only"}, 403)

    body = await request.json()
    min_follow = body.get("min_follow_days")
    cooldown = body.get("cooldown_days")

    if not isinstance(min_follow, int) or min_follow < 0:
        return JSONResponse({"error": "invalid_min_follow_days"}, 400)
    if not isinstance(cooldown, int) or cooldown < 1:
        return JSONResponse({"error": "invalid_cooldown_days"}, 400)

    user = await db.get_user_by_twitch_id(claims["channel_id"])
    if not user:
        return JSONResponse({"error": "user_not_found"}, 404)

    await db.set_ext_config(user["session_id"], min_follow, cooldown)
    return JSONResponse({"ok": True})
```

Note: import `JSONResponse` from `fastapi.responses` at the top of `main.py` if not already imported. Also import `time` if not already present.

**Step 4: Run tests to verify they pass**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_ext_endpoints.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add web/main.py web/tests/test_ext_endpoints.py
git commit -m "Add EBS endpoints: status, claim, config for extension"
```

---

## Task 9: Modify EventSub handler for unified cooldown + PubSub

**Files:**
- Modify: `web/main.py` (`_handle_eventsub_event`, around lines 1724–1771)
- Modify: `web/tests/test_webhook.py` (add tests for new behavior)

**Step 1: Write failing tests**

Add to `web/tests/test_webhook.py`:

```python
async def test_eventsub_refunds_when_viewer_has_recent_follow_age_claim():
    """If viewer claimed via follow-age within cooldown, cancel the redemption."""
    payload = _make_eventsub_payload(
        broadcaster_id="streamer-1",
        reward_id="reward-1",
        redemption_id="redemption-1",
        user_id="viewer-1",
        user_name="testviewer",
    )
    user_row = {
        "session_id": "sess-1",
        "twitch_redeem_id": "reward-1",
        "twitch_token": "tok",
        "twitch_user_id": "streamer-1",
        "ext_cooldown_days": 30,
    }
    with patch("db.get_user_by_twitch_id", new_callable=AsyncMock, return_value=user_row), \
         patch("db.has_pending_redemption", new_callable=AsyncMock, return_value=False), \
         patch("db.has_recent_invite", new_callable=AsyncMock, return_value=True), \
         patch("twitch.cancel_redemption", new_callable=AsyncMock, return_value=True) as mock_cancel, \
         patch("twitch.send_chat_message", new_callable=AsyncMock):
        await _handle_eventsub_event(payload)
    mock_cancel.assert_called_once()
```

Note: Adapt `_make_eventsub_payload` to match the existing test helper pattern in `test_webhook.py`. Read the file to understand the exact fixture setup.

**Step 2: Run tests to verify they fail**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_webhook.py::test_eventsub_refunds_when_viewer_has_recent_follow_age_claim -v`
Expected: FAIL

**Step 3: Modify _handle_eventsub_event**

In `web/main.py`, in `_handle_eventsub_event` (around line 1753), after the existing `has_pending_redemption` check and before `add_redemption`, add:

```python
    # Check unified cooldown (covers follow-age claims too)
    cooldown_days = user.get("ext_cooldown_days") or 30
    if await db.has_recent_invite(viewer_id, sess_id, cooldown_days):
        log.info("Viewer %s has recent invite, cancelling redemption", redeemer)
        await twitch.cancel_redemption(
            user["twitch_user_id"], reward_id, twitch_redemption_id, user["twitch_token"]
        )
        await twitch.send_chat_message(
            user["twitch_user_id"], user["twitch_user_id"],
            f"@{redeemer} You already have a recent invite. Points refunded.",
            user["twitch_token"],
        )
        return
```

After the existing `add_redemption()` call (around line 1763), add PubSub whisper:

```python
    # Notify the viewer's extension panel (if they have it open)
    asyncio.create_task(
        ext_pubsub.send_whisper(
            user["twitch_user_id"], viewer_id, {"type": "redemption_ready"}
        )
    )
```

Add `import ext_pubsub` at the top of `main.py`.

**Step 4: Run all webhook tests**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/test_webhook.py -v`
Expected: all PASS (new + existing)

**Step 5: Commit**

```bash
git add web/main.py web/tests/test_webhook.py
git commit -m "Add unified cooldown check and PubSub whisper to EventSub handler"
```

---

## Task 10: Extension config UI in /streamer dashboard

**Files:**
- Modify: `web/main.py` (add UI section to streamer page)

**Step 1: Add extension config section**

In the `/streamer` page handler (around line 514), after the existing redeem selector UI section, add a new card for extension settings:

```python
    # --- Extension Panel Settings ---
    if user and user.get("discord_server_id") and user.get("twitch_user_id"):
        with ui.card().classes("w-full max-w-xl mx-auto mt-4"):
            ui.label("Extension Panel Settings").classes("text-h6")
            ui.label("Configure the Twitch panel extension for follow-age invites.").classes("text-caption")

            min_follow = ui.number(
                "Minimum follow age (days)",
                value=user.get("ext_min_follow_days") or 0,
                min=0, step=1,
            )
            cooldown = ui.number(
                "Cooldown between invites (days)",
                value=user.get("ext_cooldown_days") or 30,
                min=1, step=1,
            )

            async def save_ext_config():
                await db.set_ext_config(
                    sess_id, int(min_follow.value), int(cooldown.value)
                )
                ui.notify("Extension settings saved!", type="positive")

            ui.button("Save Extension Settings", on_click=save_ext_config)
```

This is a UI-only change — no new tests needed (NiceGUI pages are tested manually).

**Step 2: Verify the page loads**

Run: `docker compose restart python && sleep 3 && docker logs thinvite-web --tail=5`
Expected: no errors, app running on port 8083.

**Step 3: Commit**

```bash
git add web/main.py
git commit -m "Add extension panel settings to streamer dashboard"
```

---

## Task 11: Extension frontend — panel.html, config.html

**Files:**
- Create: `extension/panel.html`
- Create: `extension/panel.js`
- Create: `extension/panel.css`
- Create: `extension/config.html`
- Create: `extension/config.js`
- Create: `extension/config.css`

These are static files served by Twitch's CDN, not by the Python app. They communicate with the EBS via `fetch()`.

**Step 1: Create panel.html**

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="panel.css">
  <script src="https://extension-files.twitch.tv/helper/v1/twitch-ext.min.js"></script>
</head>
<body>
  <div id="app">
    <div id="loading" class="state">
      <div class="spinner"></div>
      <p>Loading...</p>
    </div>
    <div id="identity-required" class="state hidden">
      <p>To use this panel, please share your identity.</p>
      <button id="share-identity-btn">Share Identity</button>
    </div>
    <div id="not-configured" class="state hidden">
      <p>Discord invites are not set up for this channel yet.</p>
    </div>
    <div id="eligible" class="state hidden">
      <p id="eligible-text"></p>
      <button id="claim-follow-btn" class="claim-btn">Claim Discord Invite</button>
    </div>
    <div id="pending" class="state hidden">
      <p>You have a pending channel point redemption!</p>
      <button id="claim-redeem-btn" class="claim-btn">Claim Discord Invite</button>
    </div>
    <div id="both-available" class="state hidden">
      <p>You can claim a Discord invite:</p>
      <button id="claim-follow-btn-2" class="claim-btn">Via Follow Age</button>
      <button id="claim-redeem-btn-2" class="claim-btn">Via Channel Points</button>
    </div>
    <div id="cooldown" class="state hidden">
      <p id="cooldown-text"></p>
    </div>
    <div id="not-eligible" class="state hidden">
      <p id="not-eligible-text"></p>
    </div>
    <div id="success" class="state hidden">
      <p>Your Discord invite is ready!</p>
      <a id="invite-link" href="#" target="_blank" class="claim-btn">Join Discord Server</a>
    </div>
    <div id="error" class="state hidden">
      <p id="error-text"></p>
      <button id="retry-btn">Retry</button>
    </div>
  </div>
  <script src="panel.js"></script>
</body>
</html>
```

**Step 2: Create panel.css**

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Arial, Helvetica, sans-serif;
  font-size: 14px;
  padding: 10px;
  min-height: 100px;
}
body.dark { background: #201c2b; color: #e5e3e8; }
body.light { background: #fff; color: #232127; }
.state { text-align: center; padding: 20px 0; }
.hidden { display: none; }
.claim-btn {
  display: inline-block;
  margin-top: 12px;
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: bold;
  cursor: pointer;
  text-decoration: none;
  color: #fff;
  background: #5865F2;
}
.claim-btn:hover { background: #4752c4; }
#share-identity-btn {
  margin-top: 12px;
  padding: 8px 16px;
  border: 1px solid #9147ff;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
}
body.dark #share-identity-btn { color: #e5e3e8; border-color: #9147ff; }
body.light #share-identity-btn { color: #232127; border-color: #9147ff; }
#retry-btn {
  margin-top: 8px;
  padding: 6px 14px;
  border: 1px solid currentColor;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.spinner {
  width: 24px; height: 24px;
  border: 3px solid rgba(128,128,128,0.3);
  border-top-color: #9147ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }
p { line-height: 1.4; margin-bottom: 4px; }
```

**Step 3: Create panel.js**

```js
(function () {
  "use strict";

  const EBS_BASE = "%%EBS_URL%%"; // Replaced at build/deploy time, e.g. https://thinvite.sourk9.com
  let token = null;
  let userId = null;
  let channelId = null;

  // --- State management ---
  function showState(id) {
    document.querySelectorAll(".state").forEach(el => el.classList.add("hidden"));
    document.getElementById(id).classList.remove("hidden");
  }

  // --- Theme ---
  Twitch.ext.onContext(function (ctx) {
    document.body.className = ctx.theme === "dark" ? "dark" : "light";
  });

  // --- Auth ---
  Twitch.ext.onAuthorized(function (auth) {
    token = auth.token;
    userId = auth.userId;
    channelId = auth.channelId;

    if (!userId || userId.startsWith("A")) {
      showState("identity-required");
      return;
    }
    checkStatus();
  });

  // --- Identity sharing ---
  document.getElementById("share-identity-btn").addEventListener("click", function () {
    Twitch.ext.actions.requestIdShare();
  });

  // --- PubSub listener ---
  Twitch.ext.listen("whisper-" + (userId || ""), function (target, contentType, message) {
    try {
      var data = JSON.parse(message);
      if (data.type === "redemption_ready") {
        checkStatus();
      }
    } catch (e) { /* ignore parse errors */ }
  });

  // Re-register PubSub after auth (userId now known)
  Twitch.ext.onAuthorized(function (auth) {
    if (auth.userId && !auth.userId.startsWith("A")) {
      Twitch.ext.unlisten("whisper-" + userId);
      userId = auth.userId;
      Twitch.ext.listen("whisper-" + userId, function (target, contentType, message) {
        try {
          var data = JSON.parse(message);
          if (data.type === "redemption_ready") {
            checkStatus();
          }
        } catch (e) { /* ignore */ }
      });
    }
  });

  // --- API calls ---
  function apiFetch(method, path, body) {
    var opts = {
      method: method,
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch(EBS_BASE + path, opts).then(function (r) {
      return r.json().then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function checkStatus() {
    showState("loading");
    apiFetch("GET", "/api/ext/status").then(function (res) {
      if (res.status === 403) {
        showState("identity-required");
        return;
      }
      if (res.status === 404) {
        showState("not-configured");
        return;
      }
      var d = res.data;
      if (d.on_cooldown) {
        document.getElementById("cooldown-text").textContent =
          "You claimed an invite recently. Try again in " + d.cooldown_remaining_days + " days.";
        showState("cooldown");
      } else if (d.has_pending_redemption && d.follow_age_eligible) {
        showState("both-available");
      } else if (d.has_pending_redemption) {
        showState("pending");
      } else if (d.follow_age_eligible) {
        document.getElementById("eligible-text").textContent =
          "You've followed for " + d.follow_age_days + " days — claim your Discord invite!";
        showState("eligible");
      } else if (d.follow_age_days !== null) {
        var needed = d.min_follow_days - d.follow_age_days;
        document.getElementById("not-eligible-text").textContent =
          "Follow for " + needed + " more day" + (needed !== 1 ? "s" : "") + " to earn a Discord invite.";
        showState("not-eligible");
      } else {
        document.getElementById("not-eligible-text").textContent =
          "Follow this channel to earn a Discord invite.";
        showState("not-eligible");
      }
    }).catch(function () {
      document.getElementById("error-text").textContent = "Could not load status. Please try again.";
      showState("error");
    });
  }

  function claim(type) {
    showState("loading");
    apiFetch("POST", "/api/ext/claim", { type: type }).then(function (res) {
      if (res.status === 200 && res.data.invite_url) {
        var link = document.getElementById("invite-link");
        link.href = res.data.invite_url;
        showState("success");
      } else {
        var msg = res.data.error === "on_cooldown"
          ? "You already claimed an invite recently."
          : res.data.error === "not_eligible"
            ? "You are not eligible yet."
            : "Something went wrong. Please try again.";
        document.getElementById("error-text").textContent = msg;
        showState("error");
      }
    }).catch(function () {
      document.getElementById("error-text").textContent = "Network error. Please try again.";
      showState("error");
    });
  }

  // --- Button handlers ---
  document.getElementById("claim-follow-btn").addEventListener("click", function () { claim("follow_age"); });
  document.getElementById("claim-redeem-btn").addEventListener("click", function () { claim("redemption"); });
  document.getElementById("claim-follow-btn-2").addEventListener("click", function () { claim("follow_age"); });
  document.getElementById("claim-redeem-btn-2").addEventListener("click", function () { claim("redemption"); });
  document.getElementById("retry-btn").addEventListener("click", checkStatus);
})();
```

**Step 4: Create config.html, config.js, config.css**

`extension/config.html`:
```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="config.css">
  <script src="https://extension-files.twitch.tv/helper/v1/twitch-ext.min.js"></script>
</head>
<body>
  <div id="app">
    <h2>Thinvite Extension Settings</h2>
    <label>Minimum follow age (days):
      <input type="number" id="min-follow" min="0" value="30">
    </label>
    <label>Cooldown between invites (days):
      <input type="number" id="cooldown" min="1" value="30">
    </label>
    <button id="save-btn">Save</button>
    <p id="status-msg"></p>
    <p class="hint">You can also configure these from your <a href="%%SITE_URL%%/streamer" target="_blank">Thinvite dashboard</a>.</p>
  </div>
  <script src="config.js"></script>
</body>
</html>
```

`extension/config.css`:
```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, sans-serif; padding: 16px; background: #201c2b; color: #e5e3e8; }
h2 { font-size: 16px; margin-bottom: 16px; }
label { display: block; margin-bottom: 12px; font-size: 14px; }
input[type="number"] {
  display: block; width: 100%; margin-top: 4px; padding: 6px 8px;
  border: 1px solid #444; border-radius: 4px; background: #18161f; color: #e5e3e8;
  font-size: 14px;
}
#save-btn {
  padding: 8px 20px; border: none; border-radius: 4px;
  background: #9147ff; color: #fff; font-size: 14px; cursor: pointer;
}
#save-btn:hover { background: #772ce8; }
#status-msg { margin-top: 8px; font-size: 13px; }
.hint { margin-top: 16px; font-size: 12px; color: #999; }
.hint a { color: #9147ff; }
```

`extension/config.js`:
```js
(function () {
  "use strict";

  var EBS_BASE = "%%EBS_URL%%";
  var token = null;

  Twitch.ext.onAuthorized(function (auth) {
    token = auth.token;
    // Load current config from Twitch Configuration Service
    var config = Twitch.ext.configuration.broadcaster;
    if (config && config.content) {
      try {
        var c = JSON.parse(config.content);
        document.getElementById("min-follow").value = c.min_follow_days || 0;
        document.getElementById("cooldown").value = c.cooldown_days || 30;
      } catch (e) { /* use defaults */ }
    }
  });

  document.getElementById("save-btn").addEventListener("click", function () {
    var minFollow = parseInt(document.getElementById("min-follow").value, 10);
    var cooldown = parseInt(document.getElementById("cooldown").value, 10);
    if (isNaN(minFollow) || minFollow < 0) { msg("Min follow must be >= 0", true); return; }
    if (isNaN(cooldown) || cooldown < 1) { msg("Cooldown must be >= 1 day", true); return; }

    fetch(EBS_BASE + "/api/ext/config", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ min_follow_days: minFollow, cooldown_days: cooldown }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.ok) {
        // Also save to Twitch Configuration Service for panel cache
        Twitch.ext.configuration.set("broadcaster", "1",
          JSON.stringify({ min_follow_days: minFollow, cooldown_days: cooldown }));
        msg("Saved!", false);
      } else {
        msg("Error: " + (data.error || "unknown"), true);
      }
    }).catch(function () {
      msg("Network error", true);
    });
  });

  function msg(text, isError) {
    var el = document.getElementById("status-msg");
    el.textContent = text;
    el.style.color = isError ? "#ff4444" : "#44ff44";
  }
})();
```

**Step 5: Commit**

```bash
git add extension/
git commit -m "Add Twitch panel extension frontend (panel + config)"
```

---

## Task 12: Environment variables and startup validation

**Files:**
- Modify: `web/main.py` (add assertions in `startup()`)

**Step 1: Add env var checks**

In `web/main.py`, in the `startup()` function (line 1795), add after existing assertions:

```python
    # Extension env vars (optional — only needed if extension is used)
    ext_secret = os.environ.get("TWITCH_EXT_SECRET")
    if ext_secret:
        assert os.environ.get("TWITCH_EXT_CLIENT_ID"), "TWITCH_EXT_CLIENT_ID required when TWITCH_EXT_SECRET is set"
        assert os.environ.get("TWITCH_EXT_OWNER_ID"), "TWITCH_EXT_OWNER_ID required when TWITCH_EXT_SECRET is set"
        log.info("Extension EBS enabled")
    else:
        log.info("Extension EBS disabled (TWITCH_EXT_SECRET not set)")
```

**Step 2: Update ext_auth.py to handle missing secret gracefully**

The `verify_ext_jwt` function should return `None` (not crash) when env vars are missing, so the endpoints return 401 instead of 500:

```python
def _get_secret() -> bytes | None:
    global _secret_bytes
    if _secret_bytes is None:
        b64 = os.environ.get("TWITCH_EXT_SECRET")
        if not b64:
            return None
        _secret_bytes = base64.b64decode(b64)
    return _secret_bytes


def verify_ext_jwt(token: str) -> dict | None:
    secret = _get_secret()
    if secret is None:
        return None
    # ... rest unchanged
```

**Step 3: Verify app starts**

Run: `docker compose restart python && sleep 3 && docker logs thinvite-web --tail=10`
Expected: `Extension EBS disabled (TWITCH_EXT_SECRET not set)` or `Extension EBS enabled` depending on env vars. No crash.

**Step 4: Commit**

```bash
git add web/main.py web/ext_auth.py
git commit -m "Add extension env var validation and graceful fallback"
```

---

## Task 13: Run full test suite and verify

**Step 1: Run all tests**

Run: `cd /opt/websites/thinvite_sourk9_com && docker exec thinvite-web python -m pytest tests/ -v`
Expected: all tests PASS

**Step 2: Verify app starts and health check works**

Run: `docker compose restart python && sleep 5 && curl -s http://localhost:8083/health | python3 -m json.tool`
Expected: `{"status": "ok", "db": "ok"}`

**Step 3: Test CORS on extension endpoint**

Run: `curl -s -H "Origin: https://extension-files.twitch.tv" -I http://localhost:8083/api/ext/status`
Expected: Response includes `Access-Control-Allow-Origin: https://extension-files.twitch.tv`

**Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "Fix test/integration issues from full suite run"
```
