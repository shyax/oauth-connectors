import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.oauth_google import google_authorization_url, google_exchange_code
from app.auth.oauth_slack import slack_authorization_url, slack_exchange_code
from app.auth.token_manager import encrypt_token
from app.db import get_db
from app.models import Integration
from app.observability.logging import get_logger
from app.redis_client import redis_conn

router = APIRouter(prefix="/connect", tags=["auth"])

STATE_TTL_SECONDS = 600  # 10 minutes


def _store_state(state: str, tenant_id: str, provider: str) -> None:
    redis_conn.setex(f"oauth_state:{state}", STATE_TTL_SECONDS, json.dumps({"tenant_id": tenant_id, "provider": provider}))


def _consume_state(state: str) -> dict:
    key = f"oauth_state:{state}"
    raw = redis_conn.getdel(key)  # one-time use
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    return json.loads(raw)


@router.get("/{provider}")
def start_oauth(provider: str, tenant_id: str = Query(...)):
    if provider not in ("google", "slack"):
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    state = str(uuid.uuid4())
    _store_state(state, tenant_id, provider)

    if provider == "google":
        url = google_authorization_url(state)
    else:
        url = slack_authorization_url(state)

    return RedirectResponse(url)


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    log = get_logger()
    state_data = _consume_state(state)

    if state_data["provider"] != provider:
        raise HTTPException(status_code=400, detail="Provider mismatch in state")

    tenant_id = uuid.UUID(state_data["tenant_id"])

    try:
        if provider == "google":
            token_data = google_exchange_code(code)
        else:
            token_data = slack_exchange_code(code)
    except Exception as e:
        log.error("oauth_exchange_failed", provider=provider, error=str(e))
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")

    integration = Integration(
        tenant_id=tenant_id,
        provider=provider,
        access_token=encrypt_token(token_data["access_token"]),
        refresh_token=encrypt_token(token_data["refresh_token"]) if token_data.get("refresh_token") else None,
        expires_at=token_data.get("expires_at"),
        scopes=token_data.get("scopes", []),
        status="active",
    )
    db.add(integration)
    db.commit()
    db.refresh(integration)

    log.info("integration_created", provider=provider, integration_id=str(integration.id), tenant_id=str(tenant_id))
    return {"integration_id": str(integration.id), "provider": provider, "status": "active"}
