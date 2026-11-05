"""Verified immutable audio, conversations, catalog, audit and privacy ledger."""

from alembic import op

revision = "0008_workflows"
down_revision = "0007_worker_presence"
branch_labels = None
depends_on = None


def upgrade():
    statements = """
    ALTER TABLE audio_assets DROP CONSTRAINT ck_audio_assets_status;
    ALTER TABLE audio_assets ADD CONSTRAINT ck_audio_assets_status CHECK
      (status IN ('pending','verifying','ready','invalid','expired','pending_delete','deleted'));
    ALTER TABLE audio_assets ADD COLUMN filename text NOT NULL DEFAULT 'audio';
    ALTER TABLE audio_assets ADD COLUMN verified_key text;
    ALTER TABLE audio_assets ADD COLUMN verification_job_id uuid;
    ALTER TABLE audio_assets ADD COLUMN error_code text;
    ALTER TABLE jobs DROP CONSTRAINT ck_jobs_kind;
    ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK
      (kind IN ('knowledge','planning','assessment','singing','ingestion','memory','deletion','evaluation','upload_verify'));
    CREATE TABLE conversations (
      id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title text NOT NULL, revision integer NOT NULL DEFAULT 0,
      context jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id));
    CREATE TABLE conversation_turns (
      id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      conversation_id uuid NOT NULL, idempotency_key text NOT NULL,
      request_hash text NOT NULL, user_text text NOT NULL, response jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(owner_id,conversation_id,idempotency_key),
      FOREIGN KEY(conversation_id,owner_id) REFERENCES conversations(id,owner_id) ON DELETE CASCADE);
    CREATE INDEX ix_conversations_owner ON conversations(owner_id,updated_at DESC);
    CREATE TABLE song_references (
      id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title text NOT NULL, payload jsonb NOT NULL, version integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id));
    CREATE TABLE audit_events (
      id uuid PRIMARY KEY, owner_id uuid REFERENCES users(id) ON DELETE CASCADE,
      action text NOT NULL, resource_id text, details jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now());
    CREATE INDEX ix_audit_owner ON audit_events(owner_id,created_at DESC);
    CREATE TABLE privacy_requests (
      id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      status text NOT NULL DEFAULT 'pending', error_code text, created_at timestamptz NOT NULL DEFAULT now(),
      completed_at timestamptz);
    CREATE TABLE generation_consents (
      id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      asset_id uuid NOT NULL, revision integer NOT NULL DEFAULT 1,
      statement_version text NOT NULL, revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(asset_id,owner_id) REFERENCES audio_assets(id,owner_id));
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    # Refuse a lossy downgrade when new workflows or verified assets exist.
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM conversations) OR
      EXISTS(SELECT 1 FROM song_references) OR EXISTS(SELECT 1 FROM privacy_requests) OR
      EXISTS(SELECT 1 FROM generation_consents) OR EXISTS(SELECT 1 FROM audit_events) OR
      EXISTS(SELECT 1 FROM audio_assets WHERE verified_key IS NOT NULL OR status IN ('verifying','expired')) OR
      EXISTS(SELECT 1 FROM jobs WHERE kind='upload_verify') THEN
      RAISE EXCEPTION 'export workflow data before downgrade'; END IF; END $$;""")
    for table in (
        "generation_consents",
        "privacy_requests",
        "audit_events",
        "song_references",
        "conversation_turns",
        "conversations",
    ):
        op.execute(f"DROP TABLE {table}")
    for column in ("filename", "verified_key", "verification_job_id", "error_code"):
        op.execute(f"ALTER TABLE audio_assets DROP COLUMN {column}")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT ck_jobs_kind")
    op.execute("""ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK
      (kind IN ('knowledge','planning','assessment','singing','ingestion','memory','deletion','evaluation'))""")
    op.execute("ALTER TABLE audio_assets DROP CONSTRAINT ck_audio_assets_status")
    op.execute("""ALTER TABLE audio_assets ADD CONSTRAINT ck_audio_assets_status CHECK
      (status IN ('pending','ready','invalid','pending_delete','deleted'))""")
