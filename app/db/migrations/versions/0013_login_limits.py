"""Share expiring login limits across web instances."""
from alembic import op
import sqlalchemy as sa

revision = '0013_login_limits'
down_revision = '0012_conversation_starter'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'login_limits',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_login_limits_expires_at', 'login_limits', ['expires_at'])


def downgrade():
    op.drop_table('login_limits')
