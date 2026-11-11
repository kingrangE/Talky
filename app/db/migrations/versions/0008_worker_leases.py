"""Only reclaim expired exam jobs when workers run concurrently."""
from alembic import op
import sqlalchemy as sa

revision = '0008_worker_leases'
down_revision = '0007_conversation_requests'
branch_labels = None
depends_on = None


def upgrade():
    for table, status in [('mock_exam_responses', 'processing'), ('mock_exam_reports', 'scoring')]:
        op.add_column(table, sa.Column('lease_until', sa.DateTime(timezone=True)))
        # Legacy claims are allowed a full deadline before recovery during rollout.
        op.execute(sa.text(f"UPDATE {table} SET lease_until = now() + interval '630 seconds' WHERE status = '{status}'"))


def downgrade():
    for table in ['mock_exam_responses', 'mock_exam_reports']:
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table} WHERE lease_until > now()")):
            raise RuntimeError('Drain workers before downgrade')
        op.drop_column(table, 'lease_until')
