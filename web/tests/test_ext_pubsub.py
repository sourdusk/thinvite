import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TWITCH_EXT_SECRET", "dGVzdC1zZWNyZXQta2V5LWZvci11bml0LXRlc3RzISE=")
    monkeypatch.setenv("TWITCH_EXT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TWITCH_EXT_OWNER_ID", "12345")
    # Reset cached secret
    import ext_auth
    ext_auth._secret_bytes = None


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
    body = json.loads(call_kwargs[1]["data"])
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
