"""Distinguish selected topics from user utterances."""
from alembic import op
import sqlalchemy as sa

revision = '0012_conversation_starter'
down_revision = '0011_reply_translation'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversation_turns', sa.Column('starter_topic', sa.String(32), nullable=True))


def downgrade():
    op.drop_column('conversation_turns', 'starter_topic')
