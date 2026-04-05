from urllib.parse import urlencode

import httpx

from app.config import settings

SLACK_AUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_REQUIRED_SCOPES = {"chat:write", "channels:read", "channels:history"}


def slack_authorization_url(state: str) -> str:
    params = {
        "client_id": settings.SLACK_CLIENT_ID,
        "redirect_uri": f"{settings.BASE_URL}/connect/slack/callback",
        "scope": ",".join(SLACK_REQUIRED_SCOPES),
        "state": state,
    }
    return f"{SLACK_AUTH_URL}?{urlencode(params)}"


def slack_exchange_code(code: str) -> dict:
    resp = httpx.post(
        SLACK_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.SLACK_CLIENT_ID,
            "client_secret": settings.SLACK_CLIENT_SECRET,
            "redirect_uri": f"{settings.BASE_URL}/connect/slack/callback",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise ValueError(f"Slack OAuth error: {data.get('error')}")

    granted_scopes = set(data.get("scope", "").split(","))
    missing = SLACK_REQUIRED_SCOPES - granted_scopes
    if missing:
        raise ValueError(f"Missing required Slack scopes: {missing}")

    return {
        "access_token": data["access_token"],
        "refresh_token": None,
        "expires_at": None,  # Slack bot tokens don't expire
        "scopes": list(granted_scopes),
        "team_id": data.get("team", {}).get("id"),
    }


def slack_refresh(integration) -> dict:
    # Slack bot tokens don't expire and can't be refreshed programmatically.
    # If the token is revoked, the integration must be reinstalled.
    raise ValueError("invalid_grant: Slack bot tokens cannot be refreshed — reinstall required")
