"""Commit Graph work with source writes, without coupling deletion to a live Graph DB."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006_graph_outbox'
down_revision = '0005_langfuse_prompts'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('memory_topics', postgresql.JSONB))
    op.add_column('reports', sa.Column('memory_topics', postgresql.JSONB))
    op.create_table('graph_sync_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('payload', postgresql.JSONB, nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.String(128)),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('upsert','delete','topics')", name='graph_sync_kind'))
    op.create_index('ix_graph_sync_ready', 'graph_sync_events', ['available_at', 'created_at'])
    op.create_index('ix_graph_sync_conversation', 'graph_sync_events', ['conversation_id', 'created_at', 'id'])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM graph_sync_events')):
        raise RuntimeError('Drain Graph synchronization events before downgrade')
    op.drop_table('graph_sync_events')
    op.drop_column('reports', 'memory_topics')
    op.drop_column('messages', 'memory_topics')
