"""Durable idempotent conversation requests and recoverable audio inputs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='0007_conversation_requests'
down_revision='0006_graph_outbox'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('conversation_turns',
        sa.Column('id',postgresql.UUID(as_uuid=True),primary_key=True),
        sa.Column('conversation_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('conversations.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('input_digest',sa.String(64),nullable=False),
        sa.Column('input_text',sa.Text()),sa.Column('input_audio_path',sa.Text()),sa.Column('output_audio_path',sa.Text()),
        sa.Column('status',sa.String(16),nullable=False,server_default='queued'),
        sa.Column('attempts',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('lease_until',sa.DateTime(timezone=True)),sa.Column('result',postgresql.JSONB),
        sa.Column('error_code',sa.String(128)),
        sa.Column('queue_deadline',sa.DateTime(timezone=True),nullable=False),
        sa.Column('audio_expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('queued','processing','completed','failed')",name='conversation_turn_status'))
    op.create_index('ix_conversation_turn_queue','conversation_turns',['status','queue_deadline','created_at'])
    op.create_index('one_active_conversation_turn','conversation_turns',['conversation_id'],unique=True,
                    postgresql_where=sa.text("status IN ('queued','processing')"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM conversation_turns WHERE status IN ('queued','processing')")):
        raise RuntimeError('Drain conversation requests before downgrade')
    op.drop_table('conversation_turns')
