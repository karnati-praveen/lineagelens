from __future__ import annotations

import uuid as uuid_pkg
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# pgvector is optional — only available when Postgres + pgvector extension are used.
try:
    from pgvector.sqlalchemy import Vector as _Vector
except ImportError:
    _Vector = None

_JSON_TYPE = JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")
if _Vector is not None:
    # Vector is the primary type so its Comparator (cosine_distance, l2_distance, etc.)
    # is available on PostgreSQL. SQLite falls back to JSON for storage-only use.
    _EMBEDDING_VECTOR_TYPE = _Vector(256).with_variant(JSON(), "sqlite")
else:
    _EMBEDDING_VECTOR_TYPE = JSON()

EMBEDDING_VECTOR_DIMENSION = 256


class ProvenanceRecord(Base):
    __tablename__ = "provenance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid(),
        unique=True,
        index=True,
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[uuid_pkg.UUID | None] = mapped_column(Uuid(), nullable=True, index=True)

    request_uuid: Mapped[uuid_pkg.UUID | None] = mapped_column(Uuid(), nullable=True)

    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cursor_column: Mapped[int | None] = mapped_column(Integer, nullable=True)

    timestamp_iso: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)

    prompt_messages: Mapped[dict | list | None] = mapped_column(_JSON_TYPE, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    model_parameters: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    raw_model_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    inserted_code: Mapped[str] = mapped_column(Text, nullable=False)
    surrounding_context: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    context_snapshot: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    embeddings: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    ast_snapshot: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)

    # Stored as Vector on Postgres+pgvector, JSON array on SQLite.
    embedding_vector: Mapped[list[float] | None] = mapped_column(
        _EMBEDDING_VECTOR_TYPE,
        nullable=True,
    )
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")

    lineage_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Populated by the proxy when dynamic model routing overrides the requested model.
    # Schema: {originalModel, routedModel, tier, policyId, savings_estimate_usd}
    routing_decision: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)

    # Confidence breakdown: list of EvidenceItem dicts produced by confidence_service.
    # NULL for records ingested before the confidence engine was introduced.
    # Schema: [{signal, value, weight, contribution, rationale}, ...]
    confidence_breakdown: Mapped[list | None] = mapped_column(_JSON_TYPE, nullable=True)

    provenance_payload: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_provenance_workspace_timestamp", "workspace_id", "timestamp_iso"),
        Index("ix_provenance_workspace_model", "workspace_id", "model_name"),
        Index("ix_provenance_workspace_risk", "workspace_id", "risk_score"),
    )


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid(),
        primary_key=True,
        default=uuid_pkg.uuid4,
        nullable=False,
    )
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member", server_default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refresh_token_jti: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(_JSON_TYPE, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    query: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_saved_query_workspace_user", "workspace_id", "user_id"),
        Index("ix_saved_query_workspace_created", "workspace_id", "created_at"),
    )


class ProvenanceTag(Base):
    __tablename__ = "provenance_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    record_uuid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_tag_record_tag", "record_uuid", "tag"),
        Index("ix_tag_workspace_created", "workspace_id", "created_at"),
    )


class ProvenanceComment(Base):
    __tablename__ = "provenance_comments"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    record_uuid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_comment_record_created", "workspace_id", "record_uuid", "created_at"),
    )


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    retain_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)
    redact_after_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ResourcePermission(Base):
    __tablename__ = "resource_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    record_uuid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_resource_perm_record_user", "record_uuid", "user_id"),)


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    record_uuid: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AlertConfig(Base):
    __tablename__ = "alert_configs"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False)
    trigger_on: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    scopes: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GithubIntegration(Base):
    __tablename__ = "github_integrations"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    risk_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=70)
    block_on_high_risk: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_repos: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    frequency: Mapped[str] = mapped_column(String(32), nullable=False, default="weekly")
    recipients: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    config: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OidcProvider(Base):
    __tablename__ = "oidc_providers"

    id: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid_pkg.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id: Mapped[str] = mapped_column(String(256), nullable=False)
    client_secret: Mapped[str] = mapped_column(String(512), nullable=False)
    scopes: Mapped[list] = mapped_column(_JSON_TYPE, nullable=False, default=list)
    default_role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settings: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RoutingPolicy(Base):
    """Per-workspace, per-provider dynamic model routing policy.

    Stored as one row per (workspace_id, provider) combination.  The proxy
    reads these via GET /policies/routing/internal and routes simple/standard
    requests to cheaper models according to the mappings dict.

    Example mappings value:
        {
            "simple":   "claude-haiku-4-5-20251001",
            "standard": "claude-sonnet-4-6",
            "complex":  "claude-opus-4-7"
        }
    """

    __tablename__ = "routing_policies"

    id: Mapped[uuid_pkg.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid_pkg.uuid4, nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    mappings: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_routing_policy_workspace_provider"),
        Index("ix_routing_policy_workspace", "workspace_id"),
    )
