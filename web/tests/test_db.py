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
async def test_ensure_db_user_already_exists(mock_pool_factory):
    # fetchone returns a user → no INSERT needed
    mock_pool_factory(fetchone={"id": 1, "session_id": "abc"})
    result = await db.ensure_db_user("abc")
    assert result is True


async def test_ensure_db_user_creates_new(mock_pool_factory):
    # fetchone returns None on SELECT → INSERT fires (rowcount=1)
    mock_pool_factory(fetchone=None, rowcount=1)
    result = await db.ensure_db_user("new_session")
    assert result is True


async def test_ensure_db_user_insert_fails(mock_pool_factory):
    mock_pool_factory(fetchone=None, rowcount=0)
    result = await db.ensure_db_user("new_session")
    assert result is False


# ---------------------------------------------------------------------------
# add_redemption
# ---------------------------------------------------------------------------
async def test_add_redemption_executes(mock_pool):
    _, cur = mock_pool
    await db.add_redemption("streamer_sess", "viewer_id", "viewer_name")
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "INSERT INTO redemptions" in sql
    assert params == ("streamer_sess", "viewer_id", "viewer_name")


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
# revoke_redemption
# ---------------------------------------------------------------------------
async def test_revoke_redemption_updates(mock_pool):
    _, cur = mock_pool
    await db.revoke_redemption(7, "streamer_sess")
    sql, params = cur.execute.call_args[0]
    assert "revoked_at" in sql
    assert params == (7, "streamer_sess")


async def test_revoke_redemption_includes_ownership_check(mock_pool):
    _, cur = mock_pool
    await db.revoke_redemption(42, "owner_sess")
    sql, _ = cur.execute.call_args[0]
    assert "streamer_session_id" in sql


# ---------------------------------------------------------------------------
# get_redemptions_for_streamer
# ---------------------------------------------------------------------------
async def test_get_redemptions_for_streamer(mock_pool_factory):
    rows = [{"id": 1}, {"id": 2}]
    mock_pool_factory(fetchall=rows)
    result = await db.get_redemptions_for_streamer("sess")
    assert len(result) == 2


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
