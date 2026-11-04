"""Durable jobs, idempotency, retry scheduling and worker fencing."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_jobs"
down_revision = "0002_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('knowledge','planning','assessment','singing','ingestion','memory','deletion','evaluation')",
    )
    op.add_column("jobs", sa.Column("idempotency_key", sa.String(128)))
    op.add_column("jobs", sa.Column("request_hash", sa.String(64)))
    op.add_column(
        "jobs",
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column("jobs", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("worker_token", postgresql.UUID(as_uuid=False)))
    op.add_column(
        "jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
    )
    op.add_column(
        "jobs", sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120")
    )
    op.create_unique_constraint(
        "uq_jobs_owner_idempotency", "jobs", ["owner_id", "idempotency_key"]
    )
    op.create_check_constraint(
        "ck_jobs_limits",
        "jobs",
        "max_attempts BETWEEN 1 AND 5 AND timeout_seconds BETWEEN 1 AND 600",
    )
    op.create_index(
        "ix_jobs_due",
        "jobs",
        ["available_at", "lease_until"],
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_due", table_name="jobs")
    op.drop_constraint("ck_jobs_limits", "jobs", type_="check")
    op.drop_constraint("uq_jobs_owner_idempotency", "jobs", type_="unique")
    for name in (
        "timeout_seconds",
        "max_attempts",
        "worker_token",
        "lease_until",
        "available_at",
        "request_hash",
        "idempotency_key",
    ):
        op.drop_column("jobs", name)
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('assessment','singing','ingestion','memory','deletion','evaluation')",
    )
