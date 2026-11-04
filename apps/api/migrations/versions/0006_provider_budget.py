"""A shared model account budget with an append-only per-dispatch reservation ledger."""
from alembic import op

revision = "0006_provider_budget"
down_revision = "0005_memory_skills"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE model_budget_periods (
        period text PRIMARY KEY, currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
        limit_minor bigint NOT NULL CHECK(limit_minor>=0),
        reserved_minor bigint NOT NULL DEFAULT 0 CHECK(reserved_minor>=0),
        spent_minor bigint NOT NULL DEFAULT 0 CHECK(spent_minor>=0),
        halted boolean NOT NULL DEFAULT false,
        CHECK(reserved_minor+spent_minor<=limit_minor))""")
    op.execute("""CREATE TABLE model_call_reservations (
        id uuid PRIMARY KEY, period text NOT NULL REFERENCES model_budget_periods(period),
        effect_key text NOT NULL UNIQUE, owner_id uuid REFERENCES users(id), job_id uuid,
        endpoint text NOT NULL, model text NOT NULL, pricing jsonb NOT NULL,
        input_token_bound bigint NOT NULL, output_token_bound bigint NOT NULL,
        reserved_minor bigint NOT NULL CHECK(reserved_minor>=0),
        charged_minor bigint CHECK(charged_minor>=0 AND charged_minor<=reserved_minor),
        reported_cost_minor bigint, status text NOT NULL CHECK(status IN ('reserved','dispatched','settled_usage','settled_ceiling','billing_anomaly')),
        created_at timestamptz NOT NULL DEFAULT now(), settled_at timestamptz)""")
    op.execute("CREATE INDEX ix_model_calls_owner ON model_call_reservations(owner_id,created_at DESC)")


def downgrade():
    op.drop_table("model_call_reservations")
    op.drop_table("model_budget_periods")
