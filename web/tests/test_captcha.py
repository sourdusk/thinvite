"""Tests for captcha.py — Cloudflare Turnstile server-side verification."""
import pytest
from unittest.mock import patch

import captcha
from tests.conftest import make_aiohttp_response, make_aiohttp_session


# ---------------------------------------------------------------------------
# verify_turnstile — early-exit cases (no HTTP call should be made)
# ---------------------------------------------------------------------------
async def test_empty_token_returns_false_without_http_call():
    """An empty token must be rejected before any network call."""
    with patch("aiohttp.ClientSession") as mock_cls:
        result = await captcha.verify_turnstile("")
    assert result is False
    mock_cls.assert_not_called()


async def test_none_token_returns_false_without_http_call():
    """None passed as token must be rejected before any network call."""
    with patch("aiohttp.ClientSession") as mock_cls:
        result = await captcha.verify_turnstile(None)
    assert result is False
    mock_cls.assert_not_called()


async def test_missing_secret_key_returns_false(monkeypatch):
    """If TURNSTILE_SECRET_KEY is absent the call must return False without HTTP."""
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    with patch("aiohttp.ClientSession") as mock_cls:
        result = await captcha.verify_turnstile("some-token")
    assert result is False
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# verify_turnstile — HTTP responses
# ---------------------------------------------------------------------------
async def test_success_response_returns_true():
    resp = make_aiohttp_response({"success": True}, status=200)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await captcha.verify_turnstile("valid-token")
    assert result is True


async def test_success_false_response_returns_false():
    """Cloudflare returns 200 but success=false — must return False."""
    resp = make_aiohttp_response(
        {"success": False, "error-codes": ["invalid-input-response"]},
        status=200,
    )
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await captcha.verify_turnstile("bad-token")
    assert result is False


async def test_http_500_returns_false():
    resp = make_aiohttp_response({}, status=500)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await captcha.verify_turnstile("any-token")
    assert result is False


async def test_http_403_returns_false():
    resp = make_aiohttp_response({}, status=403)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        result = await captcha.verify_turnstile("any-token")
    assert result is False


# ---------------------------------------------------------------------------
# verify_turnstile — correct request construction
# ---------------------------------------------------------------------------
async def test_posts_to_correct_url():
    """Must POST to the official Cloudflare Turnstile siteverify endpoint."""
    resp = make_aiohttp_response({"success": True}, status=200)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        await captcha.verify_turnstile("a-token")
    url = sess.post.call_args[0][0]
    assert url == "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def test_sends_secret_and_token_in_body(monkeypatch):
    """The POST body must contain the secret key and the user's token."""
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "my-secret")
    resp = make_aiohttp_response({"success": True}, status=200)
    sess = make_aiohttp_session(post_resp=resp)
    with patch("aiohttp.ClientSession", return_value=sess):
        await captcha.verify_turnstile("user-token")
    payload = sess.post.call_args[1]["data"]
    assert payload["secret"] == "my-secret"
    assert payload["response"] == "user-token"
