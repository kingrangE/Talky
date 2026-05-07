"""init schema

Revision ID: 0001_init
Revises:
Create Date: 2026-05-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("display_name", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prompt_versions.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("avg_rating", sa.Numeric(3, 2)),
        sa.Column("sample_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "one_active_prompt", "prompt_versions", ["active"],
        unique=True, postgresql_where=sa.text("active = true"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(256)),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prompt_versions.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("language", sa.String(4)),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("english_expression", sa.Text),
        sa.Column("better_expression", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="messages_role_chk"),
        sa.CheckConstraint(
            "language IS NULL OR language IN ('ko','en')", name="messages_lang_chk"
        ),
    )
    op.create_index(
        "ix_messages_conv_created", "messages", ["conversation_id", "created_at"]
    )

    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("summary", sa.Text),
        sa.Column("strengths", postgresql.JSONB),
        sa.Column("weaknesses", postgresql.JSONB),
        sa.Column("vocab_learned", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("prompt_versions.id", ondelete="SET NULL")),
        sa.Column("stars", sa.SmallInteger, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("consumed_for_evolution", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name="ratings_stars_chk"),
    )


def downgrade() -> None:
    op.drop_table("ratings")
    op.drop_table("reports")
    op.drop_index("ix_messages_conv_created", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_index("one_active_prompt", table_name="prompt_versions")
    op.drop_table("prompt_versions")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
