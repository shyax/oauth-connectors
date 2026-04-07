import uuid
from datetime import datetime

from pydantic import BaseModel


class ExternalFile(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source: str
    name: str
    metadata: dict


class ExternalMessage(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source: str
    text: str
    timestamp: str
    metadata: dict
