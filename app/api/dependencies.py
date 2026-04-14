import uuid

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.observability.logging import bind_request_context


def get_tenant_id(x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> uuid.UUID:
    try:
        tid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Tenant-ID must be a valid UUID")
    bind_request_context(tenant_id=str(tid))
    return tid


TenantDep = Depends(get_tenant_id)
DBDep = Depends(get_db)
