import uuid
from datetime import datetime

from pydantic import BaseModel


class ExternalFile(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source: str
    name: str
    metadata: dict

    model_config = {"from_attributes": True}


class ExternalMessage(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source: str
    text: str
    timestamp: str
    metadata: dict

    model_config = {"from_attributes": True}


class IntegrationOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    scopes: list[str]
    status: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConnectorJobOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    integration_id: uuid.UUID
    provider: str
    job_type: str
    status: str
    retry_count: int
    max_retries: int
    idempotency_key: str
    error_detail: str | None
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SendMessageRequest(BaseModel):
    integration_id: uuid.UUID
    channel: str
    text: str


class SendMessageResponse(BaseModel):
    job_id: uuid.UUID
    idempotency_key: str
    status: str = "enqueued"


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
