import pytest

from app.secrets_store.encryption import decrypt_text, encrypt_text


def test_encrypt_decrypt_round_trip():
    ciphertext = encrypt_text('{"api_key": "secret123"}')
    assert "secret123" not in ciphertext
    assert decrypt_text(ciphertext) == '{"api_key": "secret123"}'


def test_decrypt_rejects_garbage_input():
    with pytest.raises(ValueError):
        decrypt_text("not-a-real-token")
