"""Index the conversation list and all-status turn history in query order."""
from alembic import op
import sqlalchemy as sa

revision = '0013_conversation_read_indexes'
down_revision = '0012_conversation_starter'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_index('ix_conversations_user_started_id', 'conversations',
                    ['user_id', sa.text('started_at DESC'), 'id'])
    op.create_index('ix_conversation_turns_conversation_created_id', 'conversation_turns',
                    ['conversation_id', 'created_at', 'id'])


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index('ix_conversations_user_started_id', table_name='conversations')
    op.drop_index('ix_conversation_turns_conversation_created_id', table_name='conversation_turns')
