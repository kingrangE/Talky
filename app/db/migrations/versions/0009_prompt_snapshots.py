"""Immutable copies of successfully retrieved managed prompts."""
from alembic import op
import sqlalchemy as sa

revision = '0009_prompt_snapshots'
down_revision = '0008_worker_leases'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('prompt_snapshots',
        sa.Column('name', sa.String(512), primary_key=True),
        sa.Column('version', sa.Integer(), primary_key=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table('prompt_snapshots')
