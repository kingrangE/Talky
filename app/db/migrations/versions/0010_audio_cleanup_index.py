"""Limit repeated retention scans to rows that still reference audio."""
from alembic import op
import sqlalchemy as sa
revision = '0010_audio_cleanup_index'
down_revision = '0009_prompt_snapshots'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_conversation_audio_expiry', 'conversation_turns', ['audio_expires_at'],
                    postgresql_where=sa.text('input_audio_path IS NOT NULL OR output_audio_path IS NOT NULL'))


def downgrade():
    op.drop_index('ix_conversation_audio_expiry', table_name='conversation_turns')
