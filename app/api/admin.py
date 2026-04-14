import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import DBDep, TenantDep
from app.models import ConnectorJob, Integration
from app.normalization.schemas import ConnectorJobOut, IntegrationOut, PaginatedResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/integrations", response_model=list[IntegrationOut])
def list_integrations(
    tenant_id: Annotated[uuid.UUID, TenantDep],
    db: Annotated[Session, DBDep],
    provider: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    q = db.query(Integration).filter_by(tenant_id=tenant_id)
    if provider:
        q = q.filter_by(provider=provider)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(Integration.created_at.desc()).all()


@router.get("/jobs", response_model=PaginatedResponse)
def list_jobs(
    tenant_id: Annotated[uuid.UUID, TenantDep],
    db: Annotated[Session, DBDep],
    status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    q = db.query(ConnectorJob).filter_by(tenant_id=tenant_id)
    if status:
        q = q.filter_by(status=status)
    if provider:
        q = q.filter_by(provider=provider)
    if job_type:
        q = q.filter_by(job_type=job_type)

    total = q.count()
    jobs = q.order_by(ConnectorJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return PaginatedResponse(
        items=[ConnectorJobOut.model_validate(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=ConnectorJobOut)
def get_job(
    job_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, TenantDep],
    db: Annotated[Session, DBDep],
):
    job = db.query(ConnectorJob).filter_by(id=job_id, tenant_id=tenant_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return ConnectorJobOut.model_validate(job)
