"""Tests for db.py — all DB calls are intercepted via the mock_pool fixture."""
import pytest
import db


# ---------------------------------------------------------------------------
# get_user_by_session_id
# ---------------------------------------------------------------------------
async def test_get_user_by_session_id_found(mock_pool_factory):
    user = {"id": 1, "session_id": "abc", "twitch_user_id": "123"}
    _, cur = mock_pool_factory(fetchone=user)
    result = await db.get_user_by_session_id("abc")
    assert result == user
    cur.execute.assert_called_once()
    assert "abc" in cur.execute.call_args[0][1]


async def test_get_user_by_session_id_not_found(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await db.get_user_by_session_id("missing")
    assert result is None


# ---------------------------------------------------------------------------
# ensure_db_user
# ---------------------------------------------------------------------------
async def test_ensure_db_user_creates_new(mock_pool):
    # INSERT IGNORE fires; rowcount=1 means a new row was inserted.
    _, cur = mock_pool
    result = await db.ensure_db_user("new_session")
    assert result is True
    sql = cur.execute.call_args[0][0]
    assert "INSERT IGNORE" in sql


async def test_ensure_db_user_already_exists(mock_pool_factory):
    # INSERT IGNORE fires; rowcount=0 means the row already existed.
    # Either way the function succeeds — race-safe create-if-not-exists.
    _, cur = mock_pool_factory(rowcount=0)
    result = await db.ensure_db_user("existing_session")
    assert result is True


# ---------------------------------------------------------------------------
# add_redemption
# ---------------------------------------------------------------------------
async def test_add_redemption_executes(mock_pool):
    _, cur = mock_pool
    await db.add_redemption("streamer_sess", "viewer_id", "viewer_name")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "INSERT INTO redemptions" in sql
    assert params == ("streamer_sess", "viewer_id", "viewer_name", None, None)


async def test_add_redemption_with_twitch_ids(mock_pool):
    _, cur = mock_pool
    await db.add_redemption("streamer_sess", "viewer_id", "viewer_name", "redeem-id", "reward-id")
    sql, params = cur.execute.call_args[0]
    assert "twitch_redemption_id" in sql
    assert params == ("streamer_sess", "viewer_id", "viewer_name", "redeem-id", "reward-id")


# ---------------------------------------------------------------------------
# add_manual_redemption
# ---------------------------------------------------------------------------
async def test_add_manual_redemption_sets_flag(mock_pool):
    _, cur = mock_pool
    await db.add_manual_redemption("streamer_sess", "viewer_id", "viewer_name")
    sql, params = cur.execute.call_args[0]
    assert "is_manual" in sql
    assert "TRUE" in sql or "true" in sql.lower()


# ---------------------------------------------------------------------------
# get_pending_redemptions_for_viewer
# ---------------------------------------------------------------------------
async def test_get_pending_redemptions_returns_list(mock_pool_factory):
    rows = [
        {"id": 1, "streamer_session_id": "s1", "discord_server_id": "d1", "streamer_name": "streamer"},
    ]
    mock_pool_factory(fetchall=rows)
    result = await db.get_pending_redemptions_for_viewer("viewer_id")
    assert result == rows


async def test_get_pending_redemptions_empty(mock_pool_factory):
    mock_pool_factory(fetchall=[])
    result = await db.get_pending_redemptions_for_viewer("viewer_id")
    assert result == []


# ---------------------------------------------------------------------------
# fulfill_redemption
# ---------------------------------------------------------------------------
async def test_fulfill_redemption_updates(mock_pool):
    _, cur = mock_pool
    await db.fulfill_redemption(42, "https://discord.gg/abc")
    sql, params = cur.execute.call_args[0]
    assert "fulfilled_at" in sql
    assert params == ("https://discord.gg/abc", 42)


# ---------------------------------------------------------------------------
# has_pending_redemption
# ---------------------------------------------------------------------------
async def test_has_pending_redemption_true(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1})
    result = await db.has_pending_redemption("streamer_sess", "viewer_id")
    assert result is True


async def test_has_pending_redemption_false(mock_pool_factory):
    mock_pool_factory(fetchone=None)
    result = await db.has_pending_redemption("streamer_sess", "viewer_id")
    assert result is False


async def test_has_pending_redemption_filters_fulfilled(mock_pool):
    _, cur = mock_pool
    await db.has_pending_redemption("streamer_sess", "viewer_id")
    sql, _ = cur.execute.call_args[0]
    assert "fulfilled_at IS NULL" in sql
    assert "revoked_at IS NULL" in sql


# ---------------------------------------------------------------------------
# revoke_redemption (now returns invite_url and does SELECT + UPDATE)
# ---------------------------------------------------------------------------
async def test_revoke_redemption_updates(mock_pool):
    _, cur = mock_pool
    cur.rowcount = 1
    result = await db.revoke_redemption(7, "streamer_sess")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "revoked_at" in sql
    assert params == (7, "streamer_sess")
    assert result is True


async def test_revoke_redemption_returns_false_when_not_found(mock_pool):
    _, cur = mock_pool
    cur.rowcount = 0
    result = await db.revoke_redemption(999, "other_sess")
    assert result is False


async def test_revoke_redemption_includes_ownership_check(mock_pool):
    _, cur = mock_pool
    cur.rowcount = 1
    await db.revoke_redemption(42, "owner_sess")
    sql, _ = cur.execute.call_args[0]
    assert "streamer_session_id" in sql
    assert "revoked_at IS NULL" in sql
    assert "fulfilled_at IS NULL" in sql


# ---------------------------------------------------------------------------
# revoke_all_pending_redemptions
# ---------------------------------------------------------------------------
async def test_revoke_all_pending_returns_list(mock_pool_factory):
    pending = [{"id": 1, "invite_url": None}, {"id": 2, "invite_url": "https://discord.gg/x"}]
    _, cur = mock_pool_factory(fetchall=pending)
    result = await db.revoke_all_pending_redemptions("streamer_sess")
    assert result == pending


async def test_revoke_all_pending_issues_update(mock_pool_factory):
    pending = [{"id": 1, "invite_url": None}]
    _, cur = mock_pool_factory(fetchall=pending)
    await db.revoke_all_pending_redemptions("streamer_sess")
    assert cur.execute.call_count == 2
    update_sql, _ = cur.execute.call_args_list[1][0]
    assert "revoked_at" in update_sql


async def test_revoke_all_pending_empty_skips_update(mock_pool_factory):
    _, cur = mock_pool_factory(fetchall=[])
    await db.revoke_all_pending_redemptions("streamer_sess")
    assert cur.execute.call_count == 1  # only SELECT, no UPDATE


# ---------------------------------------------------------------------------
# get_redemptions_for_streamer
# ---------------------------------------------------------------------------
async def test_get_redemptions_for_streamer(mock_pool_factory):
    rows = [{"id": 1}, {"id": 2}]
    _, cur = mock_pool_factory(fetchall=rows)
    result = await db.get_redemptions_for_streamer("sess")
    assert len(result) == 2
    sql, params = cur.execute.call_args[0]
    assert "LIMIT" in sql.upper()
    assert params[1] == 200  # default limit


async def test_get_redemptions_for_streamer_custom_limit(mock_pool_factory):
    """A custom limit value is forwarded to the SQL query."""
    _, cur = mock_pool_factory(fetchall=[])
    await db.get_redemptions_for_streamer("sess", limit=50)
    _, params = cur.execute.call_args[0]
    assert params[1] == 50


# ---------------------------------------------------------------------------
# get_all_bot_users
# ---------------------------------------------------------------------------
async def test_get_all_bot_users(mock_pool_factory):
    users = [{"session_id": "s1"}, {"session_id": "s2"}]
    mock_pool_factory(fetchall=users)
    result = await db.get_all_bot_users()
    assert result == users


# ---------------------------------------------------------------------------
# update_twitch_auth_code
# ---------------------------------------------------------------------------
async def test_update_twitch_auth_code_success(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1}, rowcount=1)
    result = await db.update_twitch_auth_code("sess", "code123")
    assert result is True


async def test_update_twitch_auth_code_row_not_found(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1}, rowcount=0)
    result = await db.update_twitch_auth_code("sess", "code123")
    assert result is False


# ---------------------------------------------------------------------------
# update_twitch_auth_token
# ---------------------------------------------------------------------------
async def test_update_twitch_auth_token_success(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1}, rowcount=1)
    result = await db.update_twitch_auth_token("sess", "tok", 9999999, "refresh")
    assert result is True


async def test_update_twitch_auth_token_failure(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1}, rowcount=0)
    result = await db.update_twitch_auth_token("sess", "tok", 9999999, "refresh")
    assert result is False


# ---------------------------------------------------------------------------
# update_twitch_user_info
# ---------------------------------------------------------------------------
async def test_update_twitch_user_info_success(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1}, rowcount=1)
    result = await db.update_twitch_user_info("sess", "streamer1", "uid123")
    assert result is True


async def test_update_twitch_user_info_failure(mock_pool_factory):
    mock_pool_factory(fetchone={"id": 1}, rowcount=0)
    result = await db.update_twitch_user_info("sess", "streamer1", "uid123")
    assert result is False


# ---------------------------------------------------------------------------
# update_twitch_redeem
# ---------------------------------------------------------------------------
async def test_update_twitch_redeem_executes(mock_pool):
    _, cur = mock_pool
    await db.update_twitch_redeem("sess", "redeem-id-value")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "twitch_redeem_id" in sql
    assert params == ("redeem-id-value", "sess")


# ---------------------------------------------------------------------------
# disconnect_twitch
# ---------------------------------------------------------------------------
async def test_disconnect_twitch_nulls_all_fields(mock_pool):
    _, cur = mock_pool
    await db.disconnect_twitch("sess")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "twitch_user_id" in sql
    assert "NULL" in sql
    assert params == ("sess",)


async def test_disconnect_twitch_targets_correct_session(mock_pool):
    _, cur = mock_pool
    await db.disconnect_twitch("target_sess")
    _, params = cur.execute.call_args[0]
    assert "target_sess" in params


# ---------------------------------------------------------------------------
# disconnect_discord
# ---------------------------------------------------------------------------
async def test_disconnect_discord_nulls_all_fields(mock_pool):
    _, cur = mock_pool
    await db.disconnect_discord("sess")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "discord_user_id" in sql
    assert "NULL" in sql
    assert params == ("sess",)


async def test_disconnect_discord_targets_correct_session(mock_pool):
    _, cur = mock_pool
    await db.disconnect_discord("target_sess")
    _, params = cur.execute.call_args[0]
    assert "target_sess" in params


# ---------------------------------------------------------------------------
# delete_user_and_all_records
# ---------------------------------------------------------------------------
async def test_delete_user_and_all_records_issues_two_deletes(mock_pool):
    _, cur = mock_pool
    await db.delete_user_and_all_records("sess")
    assert cur.execute.call_count == 2


async def test_delete_user_and_all_records_deletes_redemptions(mock_pool):
    _, cur = mock_pool
    await db.delete_user_and_all_records("sess")
    sqls = [call[0][0] for call in cur.execute.call_args_list]
    assert any("redemptions" in sql for sql in sqls)


async def test_delete_user_and_all_records_deletes_user(mock_pool):
    _, cur = mock_pool
    await db.delete_user_and_all_records("sess")
    sqls = [call[0][0] for call in cur.execute.call_args_list]
    assert any("users" in sql for sql in sqls)


# ---------------------------------------------------------------------------
# rotate_session
# ---------------------------------------------------------------------------
async def test_rotate_session_issues_update(mock_pool):
    """rotate_session must issue exactly one UPDATE touching session_id."""
    _, cur = mock_pool
    await db.rotate_session("old_sess", "new_sess")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "UPDATE users" in sql
    assert "session_id" in sql


async def test_rotate_session_param_order(mock_pool):
    """new_id is the SET value; old_id is the WHERE predicate — never reversed.

    If the two arguments were swapped the UPDATE would match no rows (old_id
    would be used as the new value) or, worse, rename a different row to the
    caller's old token.  This test pins the correct order.
    """
    _, cur = mock_pool
    await db.rotate_session("old_id", "new_id")
    _, params = cur.execute.call_args[0]
    assert params[0] == "new_id", "first param (SET) must be the new token"
    assert params[1] == "old_id", "second param (WHERE) must be the old token"


async def test_rotate_session_does_not_use_select(mock_pool):
    """rotate_session should be a single UPDATE — no SELECT before the write."""
    _, cur = mock_pool
    await db.rotate_session("old_id", "new_id")
    for call in cur.execute.call_args_list:
        assert "SELECT" not in call[0][0].upper()


# ---------------------------------------------------------------------------
# get_expired_pending_redemptions
# ---------------------------------------------------------------------------
async def test_get_expired_pending_redemptions_returns_list(mock_pool_factory):
    rows = [
        {
            "id": 1,
            "twitch_redemption_id": "rid1",
            "twitch_reward_id": "rwid1",
            "broadcaster_id": "b1",
            "token": "tok1",
        }
    ]
    mock_pool_factory(fetchall=rows)
    result = await db.get_expired_pending_redemptions()
    assert result == rows


async def test_get_expired_pending_redemptions_empty(mock_pool_factory):
    mock_pool_factory(fetchall=[])
    result = await db.get_expired_pending_redemptions()
    assert result == []


async def test_get_expired_pending_redemptions_query_filters(mock_pool):
    """The SQL must guard against manual, fulfilled, and revoked rows."""
    _, cur = mock_pool
    await db.get_expired_pending_redemptions()
    sql = cur.execute.call_args[0][0]
    assert "fulfilled_at IS NULL" in sql
    assert "revoked_at IS NULL" in sql
    assert "is_manual = FALSE" in sql
    assert "twitch_redemption_id IS NOT NULL" in sql
    assert "INTERVAL 24 HOUR" in sql


# ---------------------------------------------------------------------------
# expire_redemption
# ---------------------------------------------------------------------------
async def test_expire_redemption_issues_update(mock_pool):
    _, cur = mock_pool
    await db.expire_redemption(42)
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "revoked_at" in sql
    assert params == (42,)


async def test_expire_redemption_targets_correct_id(mock_pool):
    _, cur = mock_pool
    await db.expire_redemption(99)
    _, params = cur.execute.call_args[0]
    assert params[0] == 99


# ---------------------------------------------------------------------------
# get_users_with_expiring_tokens
# ---------------------------------------------------------------------------
async def test_get_users_with_expiring_tokens_returns_list(mock_pool_factory):
    users = [{"session_id": "s1", "twitch_token_refresh_code": "r1"}]
    mock_pool_factory(fetchall=users)
    result = await db.get_users_with_expiring_tokens()
    assert result == users


async def test_get_users_with_expiring_tokens_empty(mock_pool_factory):
    mock_pool_factory(fetchall=[])
    result = await db.get_users_with_expiring_tokens()
    assert result == []


async def test_get_users_with_expiring_tokens_query(mock_pool):
    """SQL must filter on token presence, refresh code presence, and expiry window."""
    _, cur = mock_pool
    await db.get_users_with_expiring_tokens()
    sql = cur.execute.call_args[0][0]
    assert "twitch_auth_token IS NOT NULL" in sql
    assert "twitch_token_refresh_code IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# is_seen_eventsub_message
# ---------------------------------------------------------------------------
async def test_is_seen_eventsub_message_new(mock_pool_factory):
    """rowcount=1 after INSERT IGNORE means the row was inserted — not a duplicate."""
    _, cur = mock_pool_factory(rowcount=1)
    result = await db.is_seen_eventsub_message("msg-new")
    assert result is False
    assert cur.execute.call_count == 2  # DELETE expired + INSERT IGNORE


async def test_is_seen_eventsub_message_duplicate(mock_pool_factory):
    """rowcount=0 after INSERT IGNORE means the row already existed — duplicate."""
    _, cur = mock_pool_factory(rowcount=0)
    result = await db.is_seen_eventsub_message("msg-dup")
    assert result is True


async def test_is_seen_eventsub_message_insert_sql(mock_pool):
    """INSERT IGNORE must include the message_id param and a 10-minute expiry."""
    _, cur = mock_pool
    await db.is_seen_eventsub_message("msg-abc")
    insert_sql, params = cur.execute.call_args[0]
    assert "INSERT IGNORE" in insert_sql
    assert "INTERVAL 10 MINUTE" in insert_sql
    assert params == ("msg-abc",)


async def test_is_seen_eventsub_message_prunes_expired(mock_pool):
    """A DELETE for expired rows must fire before the INSERT IGNORE."""
    _, cur = mock_pool
    await db.is_seen_eventsub_message("msg-abc")
    first_sql = cur.execute.call_args_list[0][0][0]
    assert "DELETE" in first_sql
    assert "expires_at" in first_sql
