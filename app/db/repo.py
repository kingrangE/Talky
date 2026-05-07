"""Talky 의 Postgres 데이터 액세스 계층.

기존 ``supabase_client`` 모듈을 대체. ``client`` 인자는 더 이상 받지 않고
내부에서 ``session_scope`` 를 사용한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update

from app.db.models import Conversation, Message, PromptVersion, Rating, Report, User
from app.db.postgres import session_scope
from app.settings import get_settings


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def ensure_default_user() -> uuid.UUID:
    """단일 사용자 모드 — anonymous 사용자가 없으면 만들어 반환."""
    cfg = get_settings()
    with session_scope() as s:
        if cfg.APP_USER_ID:
            uid = _to_uuid(cfg.APP_USER_ID)
            user = s.get(User, uid)
            if user is None:
                user = User(id=uid, display_name="default")
                s.add(user)
            return user.id

        user = s.scalars(
            select(User).where(User.display_name == "anonymous").limit(1)
        ).first()
        if user is None:
            user = User(display_name="anonymous")
            s.add(user)
            s.flush()
        return user.id


def get_active_prompt() -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.scalars(
            select(PromptVersion).where(PromptVersion.active.is_(True)).limit(1)
        ).first()
        if row is None:
            return None
        return {"id": str(row.id), "version": row.version, "content": row.content}


def create_conversation(title: str = "New Conversation", user_id: uuid.UUID | None = None) -> str:
    if user_id is None:
        user_id = ensure_default_user()
    prompt = get_active_prompt()
    with session_scope() as s:
        conv = Conversation(
            user_id=user_id,
            title=title,
            prompt_version_id=_to_uuid(prompt["id"]) if prompt else None,
        )
        s.add(conv)
        s.flush()
        return str(conv.id)


def list_conversations(user_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        stmt = select(Conversation).order_by(Conversation.started_at.desc())
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        rows = s.scalars(stmt).all()
        return [
            {
                "id": str(c.id),
                "title": c.title,
                "created_at": c.started_at.isoformat() if c.started_at else None,
                "ended_at": c.ended_at.isoformat() if c.ended_at else None,
            }
            for c in rows
        ]


def delete_conversation(conversation_id: str) -> None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        conv = s.get(Conversation, cid)
        if conv is not None:
            s.delete(conv)


def update_conversation_title(conversation_id: str, title: str) -> None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        s.execute(
            update(Conversation).where(Conversation.id == cid).values(title=title)
        )


def mark_conversation_ended(conversation_id: str) -> None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        s.execute(
            update(Conversation)
            .where(Conversation.id == cid)
            .values(ended_at=datetime.now(timezone.utc))
        )


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    language: str | None = None,
    english_expression: str | None = None,
    better_expression: str | None = None,
) -> str:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        msg = Message(
            conversation_id=cid,
            role=role,
            content=content,
            language=language,
            english_expression=english_expression,
            better_expression=better_expression,
        )
        s.add(msg)
        s.flush()
        return str(msg.id)


def save_report(
    *,
    conversation_id: str,
    summary: str,
    strengths: list[str],
    weaknesses: list[str],
    vocab_learned: list[str],
) -> str:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        existing = s.scalars(select(Report).where(Report.conversation_id == cid)).first()
        if existing is not None:
            existing.summary = summary
            existing.strengths = {"items": strengths}
            existing.weaknesses = {"items": weaknesses}
            existing.vocab_learned = {"items": vocab_learned}
            s.flush()
            return str(existing.id)
        report = Report(
            conversation_id=cid,
            summary=summary,
            strengths={"items": strengths},
            weaknesses={"items": weaknesses},
            vocab_learned={"items": vocab_learned},
        )
        s.add(report)
        s.flush()
        return str(report.id)


def get_report(conversation_id: str) -> dict[str, Any] | None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        row = s.scalars(select(Report).where(Report.conversation_id == cid)).first()
        if row is None:
            return None
        return {
            "summary": row.summary,
            "strengths": (row.strengths or {}).get("items", []),
            "weaknesses": (row.weaknesses or {}).get("items", []),
            "vocab_learned": (row.vocab_learned or {}).get("items", []),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def save_rating(
    *,
    conversation_id: str,
    prompt_version_id: str | None,
    stars: int,
    comment: str | None = None,
) -> str:
    cid = _to_uuid(conversation_id)
    pvid = _to_uuid(prompt_version_id) if prompt_version_id else None
    with session_scope() as s:
        existing = s.scalars(select(Rating).where(Rating.conversation_id == cid)).first()
        if existing is not None:
            existing.stars = stars
            existing.comment = comment
            existing.prompt_version_id = pvid
            s.flush()
            return str(existing.id)
        rating = Rating(
            conversation_id=cid,
            prompt_version_id=pvid,
            stars=stars,
            comment=comment,
        )
        s.add(rating)
        s.flush()
        return str(rating.id)


def get_rating(conversation_id: str) -> dict[str, Any] | None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        row = s.scalars(select(Rating).where(Rating.conversation_id == cid)).first()
        if row is None:
            return None
        return {"stars": row.stars, "comment": row.comment}


def count_unconsumed_ratings() -> tuple[int, dict[int, int]]:
    """별점 진화에 아직 반영되지 않은 ratings 의 수와 분포."""
    with session_scope() as s:
        rows = s.scalars(
            select(Rating).where(Rating.consumed_for_evolution.is_(False))
        ).all()
        dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for r in rows:
            dist[int(r.stars)] = dist.get(int(r.stars), 0) + 1
        return len(rows), dist


def get_unconsumed_rated_reports(*, top_n: int, order: str = "desc") -> list[dict[str, Any]]:
    """별점 미반영 conversations 중 상위/하위 N개 (보고서 포함)."""
    with session_scope() as s:
        order_col = (
            Rating.stars.desc() if order == "desc" else Rating.stars.asc()
        )
        stmt = (
            select(Rating, Report)
            .join(Report, Rating.conversation_id == Report.conversation_id)
            .where(Rating.consumed_for_evolution.is_(False))
            .order_by(order_col)
            .limit(top_n)
        )
        out: list[dict[str, Any]] = []
        for rating, report in s.execute(stmt).all():
            out.append(
                {
                    "stars": int(rating.stars),
                    "comment": rating.comment,
                    "summary": report.summary,
                    "strengths": (report.strengths or {}).get("items", []),
                    "weaknesses": (report.weaknesses or {}).get("items", []),
                }
            )
        return out


def insert_prompt_version(
    *,
    content: str,
    rationale: str | None,
    parent_id: str | None,
    diff_summary: list[str] | None = None,
    activate: bool = True,
) -> str:
    pid = _to_uuid(parent_id) if parent_id else None
    rationale_full = rationale or ""
    if diff_summary:
        rationale_full = (
            rationale_full + "\n\nDiff:\n" + "\n".join(f"- {d}" for d in diff_summary)
        ).strip()

    with session_scope() as s:
        max_v = s.scalar(select(func.coalesce(func.max(PromptVersion.version), 0)))
        new_version = int(max_v or 0) + 1

        if activate:
            s.execute(
                update(PromptVersion)
                .where(PromptVersion.active.is_(True))
                .values(active=False)
            )

        pv = PromptVersion(
            version=new_version,
            content=content,
            rationale=rationale_full or None,
            parent_id=pid,
            active=activate,
        )
        s.add(pv)
        s.flush()
        return str(pv.id)


def mark_unconsumed_ratings_consumed() -> int:
    with session_scope() as s:
        result = s.execute(
            update(Rating)
            .where(Rating.consumed_for_evolution.is_(False))
            .values(consumed_for_evolution=True)
        )
        return result.rowcount or 0


def load_messages(conversation_id: str) -> list[dict[str, Any]]:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        rows = s.scalars(
            select(Message)
            .where(Message.conversation_id == cid)
            .order_by(Message.created_at)
        ).all()
        return [
            {
                "role": m.role,
                "content": m.content,
                "language": m.language,
                "english_expression": m.english_expression,
                "better_expression": m.better_expression,
            }
            for m in rows
        ]
