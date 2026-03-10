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
    # Reset cached secret between tests
    import ext_auth
    ext_auth._secret_bytes = None


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
