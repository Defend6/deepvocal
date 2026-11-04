"""Opt-in memory with source links and private versioned skill candidates."""

from alembic import op

revision = "0005_memory_skills"
down_revision = "0004_artifacts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE memory_settings (
        owner_id uuid PRIMARY KEY REFERENCES users(id), enabled boolean NOT NULL DEFAULT false,
        revision integer NOT NULL DEFAULT 1, profile jsonb NOT NULL DEFAULT '{}'::jsonb,
        updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE memory_events (
        id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES users(id), source_key text NOT NULL,
        event_kind text NOT NULL,payload jsonb NOT NULL, revision integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(owner_id,source_key), UNIQUE(id,owner_id))""")
    op.execute("CREATE INDEX ix_memory_events_owner ON memory_events(owner_id,created_at DESC)")
    op.execute("""CREATE TABLE memory_facts (
        owner_id uuid NOT NULL REFERENCES users(id), semantic_key text NOT NULL, value jsonb,
        source_event_id uuid NOT NULL, revision integer NOT NULL, status text NOT NULL CHECK(status IN ('confirmed','deleted')),
        PRIMARY KEY(owner_id,semantic_key),
        FOREIGN KEY(source_event_id,owner_id) REFERENCES memory_events(id,owner_id))""")
    op.execute("""CREATE TABLE skill_versions (
        id uuid PRIMARY KEY,owner_id uuid NOT NULL REFERENCES users(id), skill_id text NOT NULL,
        version integer NOT NULL CHECK(version>=1), agent text NOT NULL,
        status text NOT NULL CHECK(status IN ('candidate','reviewed','published','retired','rejected')),
        manifest jsonb NOT NULL, content_hash text NOT NULL, critic jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(owner_id,skill_id,version))""")


def downgrade():
    for table in ("skill_versions", "memory_facts", "memory_events", "memory_settings"):
        op.drop_table(table)
