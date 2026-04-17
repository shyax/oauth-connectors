from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.auth.token_manager import (
    decrypt_token,
    encrypt_token,
    needs_refresh,
    get_valid_access_token,
)


def test_encrypt_decrypt_roundtrip():
    plaintext = "ya29.some_access_token"
    assert decrypt_token(encrypt_token(plaintext)) == plaintext


def test_encrypt_produces_different_ciphertext_each_time():
    # Fernet uses a random IV — same plaintext must not produce same ciphertext
    t1 = encrypt_token("secret")
    t2 = encrypt_token("secret")
    assert t1 != t2


def test_needs_refresh_returns_false_when_no_expiry(active_slack_integration):
    assert needs_refresh(active_slack_integration) is False


def test_needs_refresh_returns_false_when_plenty_of_time(active_google_integration):
    assert needs_refresh(active_google_integration) is False


def test_needs_refresh_returns_true_within_buffer(expiring_google_integration):
    # expires_at is 60s from now, buffer is 300s — should trigger refresh
    assert needs_refresh(expiring_google_integration) is True


def test_needs_refresh_returns_true_for_already_expired(tenant_id):
    from app.models import Integration
    integration = Integration(
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        provider="google",
        status="active",
        access_token=encrypt_token("tok"),
        scopes=[],
        tenant_id=tenant_id,
    )
    assert needs_refresh(integration) is True


def test_get_valid_access_token_no_refresh_needed(active_google_integration):
    db = MagicMock()
    token = get_valid_access_token(active_google_integration, db)
    assert token == "ya29.fake_access"
    db.get.assert_not_called()  # refresh was not triggered


def test_refresh_marks_revoked_on_invalid_grant(expiring_google_integration, mock_redis):
    db = MagicMock()
    db.get.return_value = expiring_google_integration

    with patch("app.auth.token_manager.google_refresh", side_effect=ValueError("invalid_grant: token expired")):
        with pytest.raises(ValueError, match="invalid_grant"):
            from app.auth.token_manager import refresh_integration
            refresh_integration(expiring_google_integration.id, db)

    assert expiring_google_integration.status == "revoked"
    db.commit.assert_called()


def test_refresh_lock_not_acquired_returns_current_integration(expiring_google_integration):
    db = MagicMock()
    db.get.return_value = expiring_google_integration

    with patch("app.auth.token_manager.redis_conn") as mock_redis:
        mock_redis.set.return_value = None  # lock NOT acquired
        from app.auth.token_manager import refresh_integration
        result = refresh_integration(expiring_google_integration.id, db)

    # Should return the integration from db.get without calling the provider
    assert result == expiring_google_integration
