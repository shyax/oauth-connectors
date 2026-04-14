import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import DBDep, TenantDep
from app.models.external_object import ExternalObject
from app.normalization.schemas import ExternalFile, PaginatedResponse

router = APIRouter(prefix="/files", tags=["files"])


@router.get("", response_model=PaginatedResponse)
def list_files(
    tenant_id: Annotated[uuid.UUID, TenantDep],
    db: Annotated[Session, DBDep],
    integration_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    q = db.query(ExternalObject).filter_by(tenant_id=tenant_id, type="file")
    if integration_id:
        q = q.filter_by(integration_id=integration_id)

    total = q.count()
    objects = q.order_by(ExternalObject.last_synced_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = [
        ExternalFile(
            id=obj.id,
            tenant_id=obj.tenant_id,
            source=obj.source,
            name=obj.data.get("name", ""),
            metadata=obj.data.get("metadata", {}),
        )
        for obj in objects
    ]

    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)
