import uuid as uuid_pkg
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


EMBEDDING_VECTOR_DIMENSION = 256


class ProvenanceRecord(Base):
    __tablename__ = "provenance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        index=True,
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[uuid_pkg.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    request_uuid: Mapped[uuid_pkg.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cursor_column: Mapped[int | None] = mapped_column(Integer, nullable=True)

    timestamp_iso: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)

    prompt_messages: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    model_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_model_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    inserted_code: Mapped[str] = mapped_column(Text, nullable=False)
    surrounding_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    context_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embeddings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ast_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    embedding_vector: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_VECTOR_DIMENSION),
        nullable=True,
    )
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    lineage_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    provenance_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_provenance_workspace_timestamp", "workspace_id", "timestamp_iso"),
        Index("ix_provenance_workspace_model", "workspace_id", "model_name"),
    )


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
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
