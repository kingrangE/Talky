"""사용자별 초기 system prompt를 RDB에 저장한다.

개인화된 프롬프트는 보존하고, 기본값 변경은 새 대화용 새 버전으로 적용한다.
사용: python -m app.db.seed
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.db.models import PromptVersion, User
from app.db.postgres import session_scope
from app.db.repo import _to_uuid, ensure_default_user
from app.llm.prompt_registry import resolve_reference
from app.settings import get_settings


SEED_RATIONALE = "seed sync from prompts/seed_v1.md"


def seed_prompt_v1(session, user_id) -> PromptVersion:
    user_id = _to_uuid(user_id)
    # 사용자 행 잠금으로 첫 프롬프트의 중복 생성을 막는다.
    if session.scalars(select(User).where(User.id == user_id).with_for_update()).first() is None:
        raise ValueError("Unknown user")
    active = session.scalars(
        select(PromptVersion).where(
            PromptVersion.active.is_(True), PromptVersion.user_id == user_id
        ).limit(1)
    ).first()
    reference = None
    seed_path = Path(__file__).resolve().parents[2] / "prompts" / "seed_v1.md"
    content = seed_path.read_text(encoding="utf-8").strip()
    if active is not None:
        if get_settings().LANGFUSE_ENABLED:
            if getattr(active, "langfuse_name", None) != "talky/system":
                return active
            reference = resolve_reference("talky/system")
            if reference["version"] == active.langfuse_version:
                return active
        elif (getattr(active, 'langfuse_name', None) is not None
              or active.rationale != SEED_RATIONALE or active.content.strip() == content):
            return active
        active.active = False
        session.flush()

    if reference is None and get_settings().LANGFUSE_ENABLED:
        reference = resolve_reference("talky/system")
    max_v = session.scalars(
        select(func.coalesce(func.max(PromptVersion.version), 0))
        .where(PromptVersion.user_id == user_id)
    ).one()
    pv = PromptVersion(
        user_id=user_id,
        version=int(max_v) + 1,
        content="" if reference else content,
        langfuse_name=reference["name"] if reference else None,
        langfuse_version=reference["version"] if reference else None,
        parent_id=active.id if active is not None else None,
        rationale=SEED_RATIONALE,
        active=True,
    )
    session.add(pv)
    session.flush()
    return pv


def seed_anonymous_user(session) -> User:
    existing = session.scalars(
        select(User).where(User.display_name == "anonymous").limit(1)
    ).first()
    if existing is not None:
        return existing
    user = User(display_name="anonymous")
    session.add(user)
    session.flush()
    return user


def main() -> None:
    user_id = ensure_default_user()
    with session_scope() as s:
        prompt = seed_prompt_v1(s, user_id)
        print(f"seeded user: {user_id}")
        print(f"seeded prompt v{prompt.version}: {prompt.id}")


if __name__ == "__main__":
    main()
