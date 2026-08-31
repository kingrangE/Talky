"""초기 시드: anonymous user + prompt_versions (active).

시드가 현재 활성 버전일 때만 ``prompts/seed_v1.md`` 변경을 새 버전으로 반영한다.
별점 기반으로 진화한 프롬프트가 활성 상태라면 컨테이너 재시작 후에도 그대로 보존한다.

사용:
    python -m app.db.seed
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.db.models import PromptVersion, User
from app.db.postgres import session_scope


SEED_RATIONALE = "seed sync from prompts/seed_v1.md"


def seed_prompt_v1(session) -> PromptVersion:
    seed_path = Path(__file__).resolve().parents[2] / "prompts" / "seed_v1.md"
    content = seed_path.read_text(encoding="utf-8").strip()

    active = session.scalars(
        select(PromptVersion).where(PromptVersion.active == True).limit(1)  # noqa: E712
    ).first()
    if active is not None:
        if active.content.strip() == content:
            return active
        # 메타-LLM이 만든 활성 버전을 재시작 시 시드로 덮어쓰지 않는다.
        if active.rationale != SEED_RATIONALE:
            return active

    if active is not None:  # 활성 버전이 시드 관리 버전인 경우에만 교체
        active.active = False
        session.flush()

    max_v = session.scalars(select(func.coalesce(func.max(PromptVersion.version), 0))).one()
    pv = PromptVersion(
        version=int(max_v) + 1,
        content=content,
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
    with session_scope() as s:
        user = seed_anonymous_user(s)
        prompt = seed_prompt_v1(s)
        print(f"seeded user: {user.id}")
        print(f"seeded prompt v{prompt.version}: {prompt.id}")


if __name__ == "__main__":
    main()
