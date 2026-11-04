"""Owner-scoped reports, editable plans and user-supplied practice records."""

from alembic import op

revision = "0004_artifacts"
down_revision = "0003_jobs"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("assessment_reports", "practice_plans"):
        op.execute(f"""CREATE TABLE {table} (
            id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
            source_job_id uuid NOT NULL UNIQUE,
            payload jsonb NOT NULL, version integer NOT NULL DEFAULT 1 CHECK(version>=1),
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(id,owner_id),
            FOREIGN KEY(source_job_id,owner_id) REFERENCES jobs(id,owner_id))""")
        op.execute(f"CREATE INDEX ix_{table}_owner ON {table}(owner_id,created_at DESC)")
    op.execute("""CREATE TABLE practice_records (
        id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), plan_id uuid NOT NULL,
        idempotency_key uuid NOT NULL, request_hash text NOT NULL, plan_version integer NOT NULL,
        payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(owner_id,idempotency_key),
        FOREIGN KEY(plan_id,owner_id) REFERENCES practice_plans(id,owner_id))""")
    op.execute(
        "CREATE INDEX ix_practice_records_owner ON practice_records(owner_id,created_at DESC)"
    )


def downgrade():
    op.drop_table("practice_records")
    op.drop_table("practice_plans")
    op.drop_table("assessment_reports")
