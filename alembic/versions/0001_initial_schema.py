"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-04 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    op.execute("""
        CREATE TYPE integration_status AS ENUM ('active', 'revoked', 'error')
    """)
    op.execute("""
        CREATE TYPE job_type AS ENUM ('sync', 'action', 'webhook')
    """)
    op.execute("""
        CREATE TYPE job_status AS ENUM ('pending', 'running', 'failed', 'success', 'dead')
    """)
    op.execute("""
        CREATE TYPE external_object_type AS ENUM ('file', 'message')
    """)
    op.execute("""
        CREATE TYPE cursor_type AS ENUM ('page_token', 'timestamp')
    """)

    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("status", sa.Enum("active", "revoked", "error", name="integration_status", create_type=False), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integrations_tenant_id", "integrations", ["tenant_id"])

    op.create_table(
        "connector_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integrations.id"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("job_type", sa.Enum("sync", "action", "webhook", name="job_type", create_type=False), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.Enum("pending", "running", "failed", "success", "dead", name="job_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="5"),
        sa.Column("idempotency_key", sa.String(255), unique=True, nullable=False),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_connector_jobs_tenant_id", "connector_jobs", ["tenant_id"])
    op.create_index("ix_connector_jobs_integration_id", "connector_jobs", ["integration_id"])
    op.create_index("ix_connector_jobs_status", "connector_jobs", ["status"])

    op.create_table(
        "external_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integrations.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("type", sa.Enum("file", "message", name="external_object_type", create_type=False), nullable=False),
        sa.Column("data", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "source", "external_id", name="uq_external_object"),
    )
    op.create_index("ix_external_objects_tenant_id", "external_objects", ["tenant_id"])

    op.create_table(
        "sync_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integrations.id"), unique=True, nullable=False),
        sa.Column("cursor_type", sa.Enum("page_token", "timestamp", name="cursor_type", create_type=False), nullable=False),
        sa.Column("value", sa.String(1024), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sync_cursors")
    op.drop_table("external_objects")
    op.drop_table("connector_jobs")
    op.drop_table("integrations")
    op.execute("DROP TYPE IF EXISTS cursor_type")
    op.execute("DROP TYPE IF EXISTS external_object_type")
    op.execute("DROP TYPE IF EXISTS job_status")
    op.execute("DROP TYPE IF EXISTS job_type")
    op.execute("DROP TYPE IF EXISTS integration_status")
