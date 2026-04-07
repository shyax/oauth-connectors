import httpx

from app.connectors.base import BaseConnector, ProviderAuthError, RateLimitInfo
from app.observability.logging import get_logger

SLACK_API = "https://slack.com/api"


class SlackConnector(BaseConnector):

    def execute(self, action: str, payload: dict) -> dict:
        if action == "send_message":
            return self._send_message(payload)
        if action == "process_event":
            return self._process_event(payload)
        raise ValueError(f"Unknown action: {action}")

    def handle_rate_limit(self, response: httpx.Response) -> RateLimitInfo:
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            return RateLimitInfo(is_rate_limited=True, retry_after_seconds=retry_after)

        # Slack can also return ok=false with error=ratelimited in 200 responses
        if response.status_code == 200:
            body = response.json()
            if not body.get("ok") and body.get("error") == "ratelimited":
                retry_after = int(response.headers.get("Retry-After", 60))
                return RateLimitInfo(is_rate_limited=True, retry_after_seconds=retry_after)

        return RateLimitInfo(is_rate_limited=False)

    def _send_message(self, payload: dict) -> dict:
        token = self.get_access_token()
        resp = httpx.post(
            f"{SLACK_API}/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"channel": payload["channel"], "text": payload["text"]},
            timeout=10,
        )
        self._raise_if_rate_limited(resp)
        resp.raise_for_status()

        body = resp.json()
        self._raise_if_token_revoked(body)

        if not body.get("ok"):
            raise RuntimeError(f"Slack API error: {body.get('error')}")

        return {"message_ts": body["ts"], "channel": body["channel"]}

    def _process_event(self, payload: dict) -> dict:
        log = get_logger()
        event = payload.get("event", {})
        event_type = event.get("type")
        log.info("slack_event_processed", event_type=event_type, integration_id=str(self.integration.id))
        return {"event_type": event_type, "processed": True}

    def _raise_if_token_revoked(self, body: dict) -> None:
        if not body.get("ok") and body.get("error") in ("token_revoked", "invalid_auth", "account_inactive"):
            from app.models import Integration
            integration = self.db.get(Integration, self.integration.id)
            if integration:
                integration.status = "revoked"
                self.db.commit()
            raise ProviderAuthError(f"Slack token revoked: {body.get('error')}")
