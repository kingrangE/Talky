import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    display_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str | None] = mapped_column(String(256))
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="messages_role_chk"),
        CheckConstraint(
            "language IS NULL OR language IN ('ko','en')", name="messages_lang_chk"
        ),
        Index("ix_messages_conv_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str | None] = mapped_column(String(4))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    english_expression: Mapped[str | None] = mapped_column(Text)
    better_expression: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        Index(
            "one_active_prompt",
            "active",
            unique=True,
            postgresql_where="active = true",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avg_rating: Mapped[float | None] = mapped_column(Numeric(3, 2))
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    summary: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[dict | None] = mapped_column(JSONB)
    weaknesses: Mapped[dict | None] = mapped_column(JSONB)
    vocab_learned: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("stars BETWEEN 1 AND 5", name="ratings_stars_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    stars: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    consumed_for_evolution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MockExamSession(Base):
    __tablename__ = "mock_exam_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','scoring','completed','abandoned','failed')",
            name="mock_exam_sessions_status_chk",
        ),
        CheckConstraint(
            "scoring_profile IN ('basic','advanced')",
            name="mock_exam_sessions_profile_chk",
        ),
        Index("ix_mock_exam_sessions_visitor_started", "visitor_hash", "started_at"),
        Index("ix_mock_exam_sessions_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    visitor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    exam_set_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exam_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    current_question: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    scoring_profile: Mapped[str] = mapped_column(String(16), nullable=False, default="basic")
    consent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_question: Mapped[int | None] = mapped_column(SmallInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    responses: Mapped[list["MockExamResponse"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    report: Mapped["MockExamReport"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class MockExamResponse(Base):
    __tablename__ = "mock_exam_responses"
    __table_args__ = (
        UniqueConstraint("session_id", "question_number", name="uq_mock_exam_response_question"),
        CheckConstraint(
            "status IN ('queued','processing','scored','no_response','technical_error','failed')",
            name="mock_exam_responses_status_chk",
        ),
        Index("ix_mock_exam_responses_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mock_exam_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    encrypted_audio_path: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    audio_metrics: Mapped[dict | None] = mapped_column(JSONB)
    language_evaluation: Mapped[dict | None] = mapped_column(JSONB)
    task_evaluation: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped[MockExamSession] = relationship(back_populates="responses")


class MockExamReport(Base):
    __tablename__ = "mock_exam_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','scoring','partial','completed','failed')",
            name="mock_exam_reports_status_chk",
        ),
        Index("ix_mock_exam_reports_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mock_exam_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False)
    score_low: Mapped[int | None] = mapped_column(SmallInteger)
    score_high: Mapped[int | None] = mapped_column(SmallInteger)
    expected_level: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[str | None] = mapped_column(String(16))
    scoring_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped[MockExamSession] = relationship(back_populates="report")


class MockExamCalibrationSample(Base):
    __tablename__ = "mock_exam_calibration_samples"
    __table_args__ = (
        CheckConstraint("official_score BETWEEN 0 AND 200", name="mock_exam_official_score_chk"),
        CheckConstraint("official_score % 10 = 0", name="mock_exam_official_score_step_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    official_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    official_exam_month: Mapped[str | None] = mapped_column(String(7))
    predicted_low: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    predicted_high: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MockExamDailyAggregate(Base):
    __tablename__ = "mock_exam_daily_aggregates"
    __table_args__ = (
        UniqueConstraint("day", "scoring_profile", name="uq_mock_exam_aggregate_day_profile"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    scoring_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    started_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_processing_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    score_bands: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
