from datetime import datetime, timezone

import httpx

from app.connectors.base import BaseConnector, ProviderAuthError, RateLimitInfo
from app.models.external_object import ExternalObject, SyncCursor
from app.normalization.adapters import google_file_to_external
from app.observability.logging import get_logger

DRIVE_API = "https://www.googleapis.com/drive/v3"


class GoogleDriveConnector(BaseConnector):

    def execute(self, action: str, payload: dict) -> dict:
        if action == "list_files":
            return self._list_files(payload)
        if action == "incremental_sync":
            return self._incremental_sync(payload)
        raise ValueError(f"Unknown action: {action}")

    def handle_rate_limit(self, response: httpx.Response) -> RateLimitInfo:
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            return RateLimitInfo(is_rate_limited=True, retry_after_seconds=retry_after)

        if response.status_code == 403:
            errors = response.json().get("error", {}).get("errors", [])
            reasons = {e.get("reason") for e in errors}
            if reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
                return RateLimitInfo(is_rate_limited=True, retry_after_seconds=60)

        return RateLimitInfo(is_rate_limited=False)

    def _list_files(self, payload: dict) -> dict:
        token = self.get_access_token()
        params = {
            "pageSize": payload.get("page_size", 100),
            "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,parents)",
        }
        if payload.get("page_token"):
            params["pageToken"] = payload["page_token"]

        resp = httpx.get(
            f"{DRIVE_API}/files",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        self._raise_if_rate_limited(resp)
        self._raise_if_auth_error(resp)
        resp.raise_for_status()

        data = resp.json()
        return {
            "files": data.get("files", []),
            "next_page_token": data.get("nextPageToken"),
        }

    def _incremental_sync(self, payload: dict) -> dict:
        log = get_logger()
        integration_id = self.integration.id
        tenant_id = self.integration.tenant_id

        cursor = self.db.query(SyncCursor).filter_by(integration_id=integration_id).first()
        page_token = cursor.value if cursor else None

        synced = 0
        while True:
            result = self._list_files({"page_token": page_token, "page_size": 200})
            files = result["files"]

            for raw_file in files:
                normalized = google_file_to_external(raw_file, tenant_id)
                obj = ExternalObject(
                    tenant_id=tenant_id,
                    integration_id=integration_id,
                    source="google_drive",
                    external_id=raw_file["id"],
                    type="file",
                    data=normalized,
                    last_synced_at=datetime.now(timezone.utc),
                )
                self.db.merge(obj)
                synced += 1

            self.db.commit()

            next_token = result.get("next_page_token")
            if not next_token:
                break

            page_token = next_token
            self._persist_cursor(integration_id, "page_token", page_token)

        # Clear cursor once full page traversal completes
        self._persist_cursor(integration_id, "timestamp", datetime.now(timezone.utc).isoformat())
        log.info("google_drive_sync_complete", integration_id=str(integration_id), synced=synced)
        return {"synced": synced}

    def _persist_cursor(self, integration_id, cursor_type: str, value: str) -> None:
        cursor = self.db.query(SyncCursor).filter_by(integration_id=integration_id).first()
        if cursor:
            cursor.cursor_type = cursor_type
            cursor.value = value
        else:
            self.db.add(SyncCursor(integration_id=integration_id, cursor_type=cursor_type, value=value))
        self.db.commit()

    def _raise_if_auth_error(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise ProviderAuthError("Google returned 401 — token may be revoked")
