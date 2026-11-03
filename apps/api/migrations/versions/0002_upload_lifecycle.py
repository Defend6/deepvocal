"""Track upload expiry and completion timestamps."""

import sqlalchemy as sa
from alembic import op

revision = "0002_uploads"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_assets", sa.Column("upload_expires_at", sa.DateTime(timezone=True)))
    op.add_column("audio_assets", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_audio_assets_pending_expiry",
        "audio_assets",
        ["upload_expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_audio_assets_pending_expiry", table_name="audio_assets")
    op.drop_column("audio_assets", "completed_at")
    op.drop_column("audio_assets", "upload_expires_at")
