"""Add public_ref UUID column to attestations for non-enumerable public verify endpoint.

Revision ID: 202606140002
Revises: 202606140001
Create Date: 2026-06-14

Security fix I1: the previous public verify endpoint used the sequential integer
primary key, allowing unauthenticated callers to enumerate every attestation
across all workspaces.  This migration adds an unguessable UUID column
(public_ref) that becomes the externally-visible identifier on the public
endpoint, while the integer PK is retained for FK relationships.

Rows inserted before this migration receive a random UUID via the UPDATE below.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202606140002"
down_revision = "202606140001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add public_ref as nullable first so existing rows don't immediately violate
    # the NOT NULL constraint; we backfill before tightening.
    with op.batch_alter_table("attestations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "public_ref",
                sa.Uuid().with_variant(sa.String(36), "sqlite"),
                nullable=True,
            )
        )

    # Backfill existing rows with a random UUID.
    # gen_random_uuid() is available on Postgres 13+.
    # SQLite uses lower('hex(randomblob(4))||...') but since SQLite is test-only
    # and tests always create a fresh schema (no migration run), the UPDATE is a
    # no-op in practice there.
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        bind.execute(
            sa.text(
                "UPDATE attestations SET public_ref = gen_random_uuid() WHERE public_ref IS NULL"
            )
        )
    else:
        # SQLite: generate a pseudo-UUID string for any existing rows.
        # Tests don't run migrations (they call Base.metadata.create_all), so
        # this path only fires on a developer SQLite instance.
        import uuid as _uuid
        rows = bind.execute(sa.text("SELECT id FROM attestations WHERE public_ref IS NULL")).fetchall()
        for row in rows:
            bind.execute(
                sa.text("UPDATE attestations SET public_ref = :ref WHERE id = :id"),
                {"ref": str(_uuid.uuid4()), "id": row[0]},
            )

    # Now lock down NOT NULL and add the unique index.
    with op.batch_alter_table("attestations") as batch_op:
        batch_op.alter_column("public_ref", nullable=False)
        batch_op.create_unique_constraint("uq_attestation_public_ref", ["public_ref"])
        batch_op.create_index("ix_attestation_public_ref", ["public_ref"])


def downgrade() -> None:
    with op.batch_alter_table("attestations") as batch_op:
        batch_op.drop_index("ix_attestation_public_ref")
        batch_op.drop_constraint("uq_attestation_public_ref")
        batch_op.drop_column("public_ref")
