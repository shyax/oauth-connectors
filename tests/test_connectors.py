import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.connectors.base import ProviderAuthError, RateLimitError
from app.connectors.google_drive import GoogleDriveConnector
from app.connectors.slack import SlackConnector


class TestGoogleDriveConnector:
    def test_handle_rate_limit_on_429(self, active_google_integration):
        conn = GoogleDriveConnector(active_google_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "30"}
        info = conn.handle_rate_limit(resp)
        assert info.is_rate_limited is True
        assert info.retry_after_seconds == 30

    def test_handle_rate_limit_on_403_quota(self, active_google_integration):
        conn = GoogleDriveConnector(active_google_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403
        resp.json.return_value = {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}
        info = conn.handle_rate_limit(resp)
        assert info.is_rate_limited is True

    def test_handle_rate_limit_on_403_permission_error(self, active_google_integration):
        conn = GoogleDriveConnector(active_google_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403
        resp.json.return_value = {"error": {"errors": [{"reason": "forbidden"}]}}
        info = conn.handle_rate_limit(resp)
        assert info.is_rate_limited is False

    def test_raises_rate_limit_error_on_429(self, active_google_integration):
        conn = GoogleDriveConnector(active_google_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "60"}
        with pytest.raises(RateLimitError) as exc_info:
            conn._raise_if_rate_limited(resp)
        assert exc_info.value.retry_after_seconds == 60

    def test_raises_provider_auth_error_on_401(self, active_google_integration):
        conn = GoogleDriveConnector(active_google_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 401
        with pytest.raises(ProviderAuthError):
            conn._raise_if_auth_error(resp)

    def test_execute_unknown_action_raises(self, active_google_integration):
        conn = GoogleDriveConnector(active_google_integration, MagicMock())
        with pytest.raises(ValueError, match="Unknown action"):
            conn.execute("nonexistent_action", {})


class TestSlackConnector:
    def test_handle_rate_limit_on_429(self, active_slack_integration):
        conn = SlackConnector(active_slack_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.headers = {"Retry-After": "45"}
        info = conn.handle_rate_limit(resp)
        assert info.is_rate_limited is True
        assert info.retry_after_seconds == 45

    def test_handle_rate_limit_on_200_with_ratelimited_body(self, active_slack_integration):
        conn = SlackConnector(active_slack_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {"ok": False, "error": "ratelimited"}
        resp.headers = {"Retry-After": "10"}
        info = conn.handle_rate_limit(resp)
        assert info.is_rate_limited is True
        assert info.retry_after_seconds == 10

    def test_handle_rate_limit_on_normal_200(self, active_slack_integration):
        conn = SlackConnector(active_slack_integration, MagicMock())
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        info = conn.handle_rate_limit(resp)
        assert info.is_rate_limited is False

    def test_token_revoked_marks_integration(self, active_slack_integration):
        db = MagicMock()
        db.get.return_value = active_slack_integration
        conn = SlackConnector(active_slack_integration, db)

        with pytest.raises(ProviderAuthError):
            conn._raise_if_token_revoked({"ok": False, "error": "token_revoked"})

        assert active_slack_integration.status == "revoked"

    def test_execute_unknown_action_raises(self, active_slack_integration):
        conn = SlackConnector(active_slack_integration, MagicMock())
        with pytest.raises(ValueError, match="Unknown action"):
            conn.execute("nonexistent", {})

    def test_send_message_raises_rate_limit(self, active_slack_integration):
        db = MagicMock()
        conn = SlackConnector(active_slack_integration, db)

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "30"}

        with patch("app.connectors.slack.httpx.post", return_value=mock_resp):
            with patch.object(conn, "get_access_token", return_value="xoxb-fake"):
                with pytest.raises(RateLimitError) as exc_info:
                    conn._send_message({"channel": "#test", "text": "hello"})

        assert exc_info.value.retry_after_seconds == 30
