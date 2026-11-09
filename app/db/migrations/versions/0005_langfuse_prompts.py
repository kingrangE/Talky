"""References to Langfuse prompts; preserve all legacy prompt bodies."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0005_langfuse_prompts"
down_revision = "0004_operational_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_versions", sa.Column("langfuse_name", sa.String(512)))
    op.add_column("prompt_versions", sa.Column("langfuse_version", sa.Integer()))
    op.add_column("conversations", sa.Column("common_prompt_refs", postgresql.JSONB()))
    op.create_check_constraint("langfuse_reference_pair", "prompt_versions",
        "(langfuse_name IS NULL AND langfuse_version IS NULL) OR (langfuse_name IS NOT NULL AND langfuse_version IS NOT NULL AND langfuse_version > 0)")


def downgrade() -> None:
    # Dropping references would strand remote-only prompts; never silently lose them.
    connection = op.get_bind()
    has_prompts = connection.scalar(sa.text("SELECT count(*) FROM prompt_versions WHERE langfuse_name IS NOT NULL"))
    has_conversations = connection.scalar(sa.text("SELECT count(*) FROM conversations WHERE common_prompt_refs IS NOT NULL"))
    if has_prompts or has_conversations:
        raise RuntimeError("Export Langfuse bodies into legacy content and clear prompt/conversation references before downgrading")
    op.drop_constraint("langfuse_reference_pair", "prompt_versions", type_="check")
    op.drop_column("conversations", "common_prompt_refs")
    op.drop_column("prompt_versions", "langfuse_version")
    op.drop_column("prompt_versions", "langfuse_name")
