import jwt
import pytest

from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password


def test_hash_password_round_trip():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed)
    assert not verify_password("wrong-password", hashed)


def test_create_and_decode_access_token():
    token = create_access_token(user_id=42, email="a@b.com")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["email"] == "a@b.com"


def test_decode_rejects_tampered_token():
    token = create_access_token(user_id=1, email="a@b.com")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered)
