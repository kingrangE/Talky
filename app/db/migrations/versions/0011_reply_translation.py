"""Cache optional Korean reply translations on their original messages."""
from alembic import op
import sqlalchemy as sa

revision = '0011_reply_translation'
down_revision = '0010_audio_cleanup_index'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('korean_translation', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('messages', 'korean_translation')
