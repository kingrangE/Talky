"""Own prompt versions per user, preserving legacy conversations and ratings.

Revision ID: 0003_user_prompts
Revises: 0002_mock_exam
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_user_prompts"
down_revision = "0002_mock_exam"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_versions", sa.Column("user_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_prompt_versions_user_id", "prompt_versions", "users", ["user_id"], ["id"],
        ondelete="CASCADE",
    )
    op.drop_index("one_active_prompt", table_name="prompt_versions")
    # 기존 공통 이력을 사용자별로 복제하고 각 대화/평가/부모 링크를 재연결한다.
    op.execute("""
        CREATE TEMP TABLE user_prompt_copies ON COMMIT DROP AS
        SELECT u.id AS user_id, p.id AS old_id, gen_random_uuid() AS new_id
        FROM users u CROSS JOIN prompt_versions p
    """)
    op.execute("""
        INSERT INTO prompt_versions
            (id, user_id, version, content, rationale, parent_id, active, sample_size, created_at)
        SELECT m.new_id, m.user_id, p.version, p.content, p.rationale,
               parent.new_id, p.active, 0, p.created_at
        FROM user_prompt_copies m
        JOIN prompt_versions p ON p.id = m.old_id
        LEFT JOIN user_prompt_copies parent
            ON parent.user_id = m.user_id AND parent.old_id = p.parent_id
    """)
    op.execute("""
        UPDATE conversations c SET prompt_version_id = m.new_id
        FROM user_prompt_copies m
        WHERE c.user_id = m.user_id AND c.prompt_version_id = m.old_id
    """)
    op.execute("""
        UPDATE ratings r SET prompt_version_id = m.new_id
        FROM conversations c, user_prompt_copies m
        WHERE r.conversation_id = c.id AND c.user_id = m.user_id
          AND r.prompt_version_id = m.old_id
    """)
    op.execute("""
        UPDATE conversations c SET prompt_version_id = p.id
        FROM prompt_versions p
        WHERE c.prompt_version_id IS NULL AND c.user_id = p.user_id AND p.active
    """)
    op.execute("""
        UPDATE ratings r SET prompt_version_id = c.prompt_version_id
        FROM conversations c
        WHERE r.conversation_id = c.id AND r.prompt_version_id IS NULL
    """)
    op.execute("UPDATE prompt_versions SET active = false WHERE user_id IS NULL")
    op.create_index("ix_prompt_versions_user_id", "prompt_versions", ["user_id"])
    op.create_index(
        "one_active_prompt_per_user", "prompt_versions", ["user_id"],
        unique=True, postgresql_where=sa.text("active = true"),
    )
    op.create_check_constraint(
        "active_prompt_has_owner", "prompt_versions", "NOT active OR user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint("active_prompt_has_owner", "prompt_versions", type_="check")
    op.drop_index("one_active_prompt_per_user", table_name="prompt_versions")
    # 공통 스키마로 돌아갈 때 최신 활성 버전 하나만 유지하며 버전 본문은 보존한다.
    op.execute("""
        UPDATE prompt_versions SET active = false
        WHERE active AND id <> (
            SELECT id FROM prompt_versions WHERE active ORDER BY created_at DESC, id LIMIT 1
        )
    """)
    op.drop_index("ix_prompt_versions_user_id", table_name="prompt_versions")
    op.drop_constraint("fk_prompt_versions_user_id", "prompt_versions", type_="foreignkey")
    op.drop_column("prompt_versions", "user_id")
    op.create_index(
        "one_active_prompt", "prompt_versions", ["active"],
        unique=True, postgresql_where=sa.text("active = true"),
    )
