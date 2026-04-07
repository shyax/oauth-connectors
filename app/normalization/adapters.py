import uuid
from datetime import datetime


def google_file_to_external(raw: dict, tenant_id: uuid.UUID) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "source": "google_drive",
        "name": raw.get("name", ""),
        "metadata": {
            "mime_type": raw.get("mimeType"),
            "size": raw.get("size"),
            "modified_at": raw.get("modifiedTime"),
            "parents": raw.get("parents", []),
        },
    }


def slack_message_to_external(raw: dict, tenant_id: uuid.UUID) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "source": "slack",
        "text": raw.get("text", ""),
        "timestamp": raw.get("ts"),
        "metadata": {
            "channel": raw.get("channel"),
            "user": raw.get("user"),
            "thread_ts": raw.get("thread_ts"),
        },
    }
