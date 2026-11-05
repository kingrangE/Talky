"""Account login, durable work attempts, rating snapshots and audio deletion outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0004_operational_safety"
down_revision = "0003_user_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("login_name", sa.String(64)))
    op.add_column("users", sa.Column("password_hash", sa.Text()))
    op.add_column("users", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_users_login_name", "users", ["login_name"])
    op.add_column("ratings", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    for table in ("mock_exam_responses", "mock_exam_reports"):
        op.add_column(table, sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "pending_audio_deletions",
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pending_audio_deletions")
    for table in ("mock_exam_reports", "mock_exam_responses"):
        op.drop_column(table, "attempts")
    op.drop_column("ratings", "revision")
    op.drop_constraint("uq_users_login_name", "users", type_="unique")
    for column in ("locked_until", "failed_logins", "password_hash", "login_name"):
        op.drop_column("users", column)
