"""초기 시드: anonymous user + prompt_versions v1 (active=true).

사용:
    python -m app.db.seed
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db.models import PromptVersion, User
from app.db.postgres import session_scope


def seed_prompt_v1(session) -> PromptVersion:
    existing = session.scalars(select(PromptVersion).limit(1)).first()
    if existing is not None:
        return existing

    seed_path = Path(__file__).resolve().parents[2] / "prompts" / "seed_v1.md"
    content = seed_path.read_text(encoding="utf-8").strip()
    pv = PromptVersion(
        version=1,
        content=content,
        rationale="initial seed prompt",
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
