"""Create the first operational business tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'pending_delete')", name="ck_users_status"
        ),
    )
    op.create_table(
        "audio_assets",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_audio_assets_id_owner"),
        sa.CheckConstraint("sha256 ~ '^[a-f0-9]{64}$'", name="ck_audio_assets_sha256"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 104857600", name="ck_audio_assets_size"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms BETWEEN 1 AND 600000",
            name="ck_audio_assets_duration",
        ),
        sa.CheckConstraint(
            "purpose IN ('assessment', 'voice_sample', 'melody', 'reference', 'generated_reference')",
            name="ck_audio_assets_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'invalid', 'pending_delete', 'deleted')",
            name="ck_audio_assets_status",
        ),
    )
    op.create_index("ix_audio_assets_owner_created", "audio_assets", ["owner_id", "created_at"])
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("input_asset_id", postgresql.UUID(as_uuid=False)),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error_code", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_jobs_id_owner"),
        sa.ForeignKeyConstraint(
            ["input_asset_id", "owner_id"],
            ["audio_assets.id", "audio_assets.owner_id"],
            name="fk_jobs_asset_owner",
        ),
        sa.CheckConstraint(
            "kind IN ('assessment', 'singing', 'ingestion', 'memory', 'deletion', 'evaluation')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("attempt >= 0 AND version >= 1", name="ck_jobs_attempt_version"),
    )
    op.create_index("ix_jobs_owner_created", "jobs", ["owner_id", "created_at"])
    op.create_index("ix_jobs_ready", "jobs", ["status", "created_at"])
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox_events",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_index("ix_jobs_ready", table_name="jobs")
    op.drop_index("ix_jobs_owner_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_audio_assets_owner_created", table_name="audio_assets")
    op.drop_table("audio_assets")
    op.drop_table("users")
