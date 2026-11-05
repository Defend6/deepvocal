"""Long-running local inference has an explicit bounded deadline."""
from alembic import op

revision = "0009_inference_limits"
down_revision = "0008_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_limits")
    op.execute("""ALTER TABLE jobs ADD CONSTRAINT ck_jobs_limits CHECK
      (max_attempts BETWEEN 1 AND 5 AND timeout_seconds BETWEEN 1 AND 1800)""")


def downgrade():
    op.execute("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_limits")
    op.execute("""ALTER TABLE jobs ADD CONSTRAINT ck_jobs_limits CHECK
      (max_attempts BETWEEN 1 AND 5 AND timeout_seconds BETWEEN 1 AND 600)""")
