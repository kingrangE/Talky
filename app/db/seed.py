"""초기 시드: anonymous user + prompt_versions (active).

`prompts/seed_v1.md` 의 내용이 현재 active prompt 와 다르면 새 버전을 추가하고 activate 한다.
즉 도커 재기동 시 파일 변경이 자동으로 반영된다.

사용:
    python -m app.db.seed
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.db.models import PromptVersion, User
from app.db.postgres import session_scope


def seed_prompt_v1(session) -> PromptVersion:
    seed_path = Path(__file__).resolve().parents[2] / "prompts" / "seed_v1.md"
    content = seed_path.read_text(encoding="utf-8").strip()

    active = session.scalars(
        select(PromptVersion).where(PromptVersion.active == True).limit(1)  # noqa: E712
    ).first()
    if active is not None and active.content.strip() == content:
        return active

    if active is not None:
        active.active = False
        session.flush()

    max_v = session.scalars(select(func.coalesce(func.max(PromptVersion.version), 0))).one()
    pv = PromptVersion(
        version=int(max_v) + 1,
        content=content,
        rationale="seed sync from prompts/seed_v1.md",
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
