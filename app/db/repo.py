"""Talky 의 Postgres 데이터 액세스 계층.

기존 ``supabase_client`` 모듈을 대체. ``client`` 인자는 더 이상 받지 않고
내부에서 ``session_scope`` 를 사용한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

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
