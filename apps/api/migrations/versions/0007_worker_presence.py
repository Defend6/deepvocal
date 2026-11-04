"""Persist worker liveness independently from long-running job handlers."""

from alembic import op

revision = "0007_worker_presence"
down_revision = "0006_provider_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE worker_heartbeats (
        consumer text PRIMARY KEY,
        started_at timestamptz NOT NULL DEFAULT now(),
        seen_at timestamptz NOT NULL DEFAULT now(),
        last_tick_at timestamptz,
        phase text NOT NULL CHECK(phase IN ('idle','busy','degraded','stopped')),
        active_job_id uuid,
        last_error_code text CHECK(last_error_code IN ('DATABASE_UNAVAILABLE','QUEUE_UNAVAILABLE'))
    )""")
    op.execute("CREATE INDEX ix_workers_seen ON worker_heartbeats(seen_at)")


def downgrade():
    op.drop_table("worker_heartbeats")
