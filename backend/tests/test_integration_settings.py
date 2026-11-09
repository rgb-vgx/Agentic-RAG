"""Integration credential encryption helpers."""

from app.services.integration_settings import (
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)


def test_round_trip_encryption():
    encrypted = encrypt_secret("tvly-secret-key")
    assert encrypted != "tvly-secret-key"
    assert decrypt_secret(encrypted) == "tvly-secret-key"


def test_mask_keeps_only_edges():
    masked = mask_secret("tvly-1234567890")
    assert masked.startswith("tvly-")
    assert masked.endswith("7890")
    assert "123456" not in masked
