"""speaking mock test schema

Revision ID: 0002_mock_exam
Revises: 0001_init
Create Date: 2026-08-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_mock_exam"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "mock_exam_sessions",
        _uuid(),
        sa.Column("visitor_hash", sa.String(64), nullable=False),
        sa.Column("exam_set_id", sa.String(128), nullable=False),
        sa.Column("exam_set_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("current_question", sa.SmallInteger, nullable=False, server_default="1"),
        sa.Column("scoring_profile", sa.String(16), nullable=False, server_default="basic"),
        sa.Column("consent_version", sa.String(32), nullable=False),
        sa.Column("retry_question", sa.SmallInteger),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','scoring','completed','abandoned','failed')",
            name="mock_exam_sessions_status_chk",
        ),
        sa.CheckConstraint(
            "scoring_profile IN ('basic','advanced')",
            name="mock_exam_sessions_profile_chk",
        ),
    )
    op.create_index(
        "ix_mock_exam_sessions_visitor_started",
        "mock_exam_sessions",
        ["visitor_hash", "started_at"],
    )
    op.create_index("ix_mock_exam_sessions_expires", "mock_exam_sessions", ["expires_at"])

    op.create_table(
        "mock_exam_responses",
        _uuid(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mock_exam_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_number", sa.SmallInteger, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("encrypted_audio_path", sa.Text),
        sa.Column("transcript", sa.Text),
        sa.Column("audio_metrics", postgresql.JSONB),
        sa.Column("language_evaluation", postgresql.JSONB),
        sa.Column("task_evaluation", postgresql.JSONB),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "session_id", "question_number", name="uq_mock_exam_response_question"
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','scored','no_response','technical_error','failed')",
            name="mock_exam_responses_status_chk",
        ),
    )
    op.create_index("ix_mock_exam_responses_expires", "mock_exam_responses", ["expires_at"])

    op.create_table(
        "mock_exam_reports",
        _uuid(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mock_exam_sessions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("scoring_version", sa.String(32), nullable=False),
        sa.Column("score_low", sa.SmallInteger),
        sa.Column("score_high", sa.SmallInteger),
        sa.Column("expected_level", sa.String(64)),
        sa.Column("confidence", sa.String(16)),
        sa.Column("scoring_profile", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','scoring','partial','completed','failed')",
            name="mock_exam_reports_status_chk",
        ),
    )
    op.create_index("ix_mock_exam_reports_expires", "mock_exam_reports", ["expires_at"])

    op.create_table(
        "mock_exam_calibration_samples",
        _uuid(),
        sa.Column("official_score", sa.SmallInteger, nullable=False),
        sa.Column("official_exam_month", sa.String(7)),
        sa.Column("predicted_low", sa.SmallInteger, nullable=False),
        sa.Column("predicted_high", sa.SmallInteger, nullable=False),
        sa.Column("scoring_version", sa.String(32), nullable=False),
        sa.Column("scoring_profile", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("official_score BETWEEN 0 AND 200", name="mock_exam_official_score_chk"),
        sa.CheckConstraint("official_score % 10 = 0", name="mock_exam_official_score_step_chk"),
    )

    op.create_table(
        "mock_exam_daily_aggregates",
        _uuid(),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("scoring_profile", sa.String(16), nullable=False),
        sa.Column("started_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_processing_ms", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("score_bands", postgresql.JSONB),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("day", "scoring_profile", name="uq_mock_exam_aggregate_day_profile"),
    )


def downgrade() -> None:
    op.drop_table("mock_exam_daily_aggregates")
    op.drop_table("mock_exam_calibration_samples")
    op.drop_index("ix_mock_exam_reports_expires", table_name="mock_exam_reports")
    op.drop_table("mock_exam_reports")
    op.drop_index("ix_mock_exam_responses_expires", table_name="mock_exam_responses")
    op.drop_table("mock_exam_responses")
    op.drop_index("ix_mock_exam_sessions_expires", table_name="mock_exam_sessions")
    op.drop_index("ix_mock_exam_sessions_visitor_started", table_name="mock_exam_sessions")
    op.drop_table("mock_exam_sessions")
