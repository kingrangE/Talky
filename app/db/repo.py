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
from app.llm.prompt_registry import common_references, publish_user_prompt, read_reference


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _owned_conversation(session, conversation_id, user_id, *, lock=False) -> Conversation:
    stmt = select(Conversation).where(
        Conversation.id == _to_uuid(conversation_id),
        Conversation.user_id == _to_uuid(user_id),
    )
    if lock:
        stmt = stmt.with_for_update()
    conversation = session.scalars(stmt).first()
    if conversation is None:
        raise ValueError("Conversation does not belong to user")
    return conversation


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


def get_active_prompt(user_id: str | uuid.UUID) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.scalars(
            select(PromptVersion).where(
                PromptVersion.active.is_(True), PromptVersion.user_id == _to_uuid(user_id)
            ).limit(1)
        ).first()
        if row is None:
            return None
        result = _prompt_record(row)
    return _hydrate_prompt(result)


def _prompt_record(row: PromptVersion) -> dict:
    reference = {"source": "langfuse", "name": row.langfuse_name, "version": row.langfuse_version} if row.langfuse_name else None
    return {"id": str(row.id), "version": row.version, "content": row.content, "prompt_ref": reference}


def _hydrate_prompt(result: dict) -> dict:
    if result["prompt_ref"]:
        result["content"] = read_reference(result["prompt_ref"])
    return result


def get_conversation_prompt(conversation_id: str, user_id: str | uuid.UUID) -> dict[str, Any] | None:
    """소유권을 확인한 뒤 고정된 이름·버전으로 프롬프트를 읽는다."""
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        conversation = s.scalars(
            select(Conversation).where(Conversation.id == cid, Conversation.user_id == _to_uuid(user_id)).with_for_update()
        ).first()
        if conversation is None:
            return None
        row = s.get(PromptVersion, conversation.prompt_version_id) if conversation.prompt_version_id else None
        if row is None or row.user_id != _to_uuid(user_id):
            return None
        if conversation.common_prompt_refs is None:
            # Legacy conversations used code constants, so do not silently adopt a new Cloud label.
            conversation.common_prompt_refs = common_references(local=True)
        result = {**_prompt_record(row), "common_prompt_refs": conversation.common_prompt_refs}
    return _hydrate_prompt(result)


def create_conversation(title: str = "New Conversation", *, user_id: str | uuid.UUID) -> str:
    user_id = _to_uuid(user_id)
    from app.db.seed import seed_prompt_v1

    with session_scope() as s:
        seed_prompt_v1(s, user_id)
    prompt = get_active_prompt(user_id)
    if prompt is None:
        raise ValueError("Active system prompt not found for user")
    references = common_references()
    with session_scope() as s:
        conv = Conversation(
            user_id=user_id,
            title=title,
            prompt_version_id=_to_uuid(prompt["id"]) if prompt else None,
            common_prompt_refs=references,
        )
        s.add(conv)
        s.flush()
        return str(conv.id)


def list_conversations(user_id: str | uuid.UUID, *, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as s:
        stmt = (select(Conversation).where(Conversation.user_id == _to_uuid(user_id))
                .order_by(Conversation.started_at.desc(), Conversation.id)
                .offset(max(0, offset)).limit(max(1, min(limit, 100))))
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


def delete_conversation(conversation_id: str, user_id: str | uuid.UUID) -> None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        conv = _owned_conversation(s, cid, user_id, lock=True)
        s.delete(conv)
    if user_id:
        # Postgres 삭제가 커밋된 뒤 Neo4j 사본도 정리한다. Neo4j 장애는 내부에서
        # 기록하고 False를 반환하므로 사용자 대화 삭제 자체는 유지된다.
        from app.graph_db.ingest import delete_conversation_memory

        delete_conversation_memory(conversation_id=str(cid), user_id=str(user_id))


def update_conversation_title(conversation_id: str, title: str, user_id: str | uuid.UUID) -> None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        conv = _owned_conversation(s, cid, user_id, lock=True)
        conv.title = title.strip()[:256]


def save_turn(
    conversation_id: str,
    user_id: str | uuid.UUID,
    user_text: str,
    ai_reply: str,
    *,
    language: str | None = None,
    english_expression: str | None = None,
    better_expression: str | None = None,
) -> tuple[str, str]:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        conv = _owned_conversation(s, cid, user_id, lock=True)
        if conv.ended_at is not None:
            raise ValueError("Conversation has ended")
        user_msg = Message(conversation_id=cid, role="user", content=user_text,
                           language=language, english_expression=english_expression)
        ai_msg = Message(conversation_id=cid, role="assistant", content=ai_reply,
                         language=language, better_expression=better_expression)
        s.add_all([user_msg, ai_msg])
        s.flush()
        return str(user_msg.id), str(ai_msg.id)


def save_report(
    *,
    conversation_id: str,
    user_id: str | uuid.UUID,
    summary: str,
    strengths: list[str],
    weaknesses: list[str],
    vocab_learned: list[str],
    ended: bool = False,
) -> str:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        conv = _owned_conversation(s, cid, user_id, lock=True)
        if ended:
            conv.ended_at = datetime.now(timezone.utc)
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


def get_report(conversation_id: str, user_id: str | uuid.UUID) -> dict[str, Any] | None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        _owned_conversation(s, cid, user_id)
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
    user_id: str | uuid.UUID,
    prompt_version_id: str | None,
    stars: int,
    comment: str | None = None,
) -> str:
    cid = _to_uuid(conversation_id)
    uid = _to_uuid(user_id)
    with session_scope() as s:
        conv = _owned_conversation(s, cid, uid, lock=True)
        prompt = s.get(PromptVersion, conv.prompt_version_id) if conv.prompt_version_id else None
        if prompt is None or prompt.user_id != uid:
            raise ValueError("Conversation has no user-owned system prompt")
        pvid = prompt.id
        if prompt_version_id is not None and _to_uuid(prompt_version_id) != pvid:
            raise ValueError("Rating must use the conversation's system prompt")
        existing = s.scalars(select(Rating).where(Rating.conversation_id == cid)).first()
        if existing is not None:
            if existing.stars == stars and existing.comment == comment:
                return str(existing.id)
            existing.stars = stars
            existing.comment = comment
            existing.prompt_version_id = pvid
            existing.consumed_for_evolution = False
            existing.revision += 1
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


def get_rating(conversation_id: str, user_id: str | uuid.UUID) -> dict[str, Any] | None:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        _owned_conversation(s, cid, user_id)
        row = s.scalars(select(Rating).where(Rating.conversation_id == cid)).first()
        if row is None:
            return None
        return {"stars": row.stars, "comment": row.comment}


def count_unconsumed_ratings(
    user_id: str | uuid.UUID,
    prompt_version_id: str,
) -> tuple[int, dict[int, int]]:
    """별점 진화에 아직 반영되지 않은 ratings 의 수와 분포."""
    with session_scope() as s:
        stmt = select(Rating).join(Conversation).where(
            Rating.consumed_for_evolution.is_(False),
            Conversation.user_id == _to_uuid(user_id),
            Rating.prompt_version_id == _to_uuid(prompt_version_id),
        )
        rows = s.scalars(stmt).all()
        dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for r in rows:
            dist[int(r.stars)] = dist.get(int(r.stars), 0) + 1
        return len(rows), dist


def get_unconsumed_rated_reports(
    *, user_id: str | uuid.UUID, top_n: int | None, order: str = "desc", prompt_version_id: str
) -> list[dict[str, Any]]:
    """별점 미반영 conversations 중 상위/하위 N개 (보고서 포함)."""
    with session_scope() as s:
        order_col = (
            Rating.stars.desc() if order == "desc" else Rating.stars.asc()
        )
        stmt = (
            select(Rating, Report)
            .join(Report, Rating.conversation_id == Report.conversation_id)
            .join(Conversation, Rating.conversation_id == Conversation.id)
            .where(
                Rating.consumed_for_evolution.is_(False),
                Conversation.user_id == _to_uuid(user_id),
                Rating.prompt_version_id == _to_uuid(prompt_version_id),
            )
        )
        stmt = stmt.order_by(order_col).limit(top_n)
        out: list[dict[str, Any]] = []
        for rating, report in s.execute(stmt).all():
            out.append(
                {
                    "id": str(rating.id),
                    "revision": rating.revision,
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
    user_id: str | uuid.UUID,
    content: str,
    rationale: str | None,
    parent_id: str | None,
    diff_summary: list[str] | None = None,
    activate: bool = True,
    rating_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    uid = _to_uuid(user_id)
    pid = _to_uuid(parent_id) if parent_id else None
    rationale_full = rationale or ""
    if diff_summary:
        rationale_full = (
            rationale_full + "\n\nDiff:\n" + "\n".join(f"- {d}" for d in diff_summary)
        ).strip()

    with session_scope() as s:
        if s.scalars(select(User).where(User.id == uid).with_for_update()).first() is None:
            raise ValueError("Unknown user")
        parent = s.get(PromptVersion, pid) if pid else None
        if pid and (parent is None or parent.user_id != uid):
            raise ValueError("Parent prompt does not belong to user")
        if rating_snapshot is not None:
            if not parent or not parent.active or not rating_snapshot:
                raise ValueError("Prompt or ratings changed during evolution")
            expected = {_to_uuid(item["id"]): item["revision"] for item in rating_snapshot}
            ratings = s.scalars(select(Rating).where(
                Rating.id.in_(expected), Rating.prompt_version_id == pid,
                Rating.consumed_for_evolution.is_(False),
                Rating.conversation_id.in_(select(Conversation.id).where(Conversation.user_id == uid)),
            ).with_for_update()).all()
            if {rating.id: rating.revision for rating in ratings} != expected:
                raise ValueError("Ratings changed during evolution")
        max_v = s.scalar(
            select(func.coalesce(func.max(PromptVersion.version), 0))
            .where(PromptVersion.user_id == uid)
        )
        new_version = int(max_v or 0) + 1

        if activate:
            s.execute(
                update(PromptVersion)
                .where(PromptVersion.active.is_(True), PromptVersion.user_id == uid)
                .values(active=False)
            )

        record_id = uuid.uuid4()
        reference = publish_user_prompt(str(uid), content, record_id=str(record_id)) if get_settings().LANGFUSE_ENABLED else None
        pv = PromptVersion(
            id=record_id,
            user_id=uid,
            version=new_version,
            content="" if reference else content,
            langfuse_name=reference["name"] if reference else None,
            langfuse_version=reference["version"] if reference else None,
            rationale=rationale_full or None,
            parent_id=pid,
            active=activate,
        )
        s.add(pv)
        s.flush()
        if rating_snapshot is not None:
            for rating in ratings:
                rating.consumed_for_evolution = True
            s.flush()
        return str(pv.id)


def rollback_active_prompt_if_degraded(
    *, user_id: str | uuid.UUID, min_samples: int, drop_threshold: float
) -> str | None:
    """활성 prompt가 부모보다 충분히 나쁘면 부모 버전을 다시 활성화한다."""
    with session_scope() as s:
        active = s.scalars(
            select(PromptVersion).where(
                PromptVersion.active.is_(True), PromptVersion.user_id == _to_uuid(user_id)
            ).limit(1)
        ).first()
        if active is None or active.parent_id is None:
            return None
        parent = s.get(PromptVersion, active.parent_id)
        if parent is None or parent.user_id != _to_uuid(user_id):
            return None

        def rating_stats(prompt_id: uuid.UUID) -> tuple[int, float | None]:
            count, average = s.execute(
                select(func.count(Rating.id), func.avg(Rating.stars)).join(Conversation).where(
                    Conversation.user_id == _to_uuid(user_id),
                    Rating.prompt_version_id == prompt_id
                )
            ).one()
            return int(count or 0), float(average) if average is not None else None

        active_count, active_avg = rating_stats(active.id)
        parent_count, parent_avg = rating_stats(parent.id)
        active.sample_size, active.avg_rating = active_count, active_avg
        parent.sample_size, parent.avg_rating = parent_count, parent_avg

        if (
            active_count < min_samples
            or parent_count < min_samples
            or active_avg is None
            or parent_avg is None
            or active_avg > parent_avg - drop_threshold
        ):
            return None

        # partial unique index(one_active_prompt_per_user)를 지키기 위해 순서대로 flush한다.
        active.active = False
        s.flush()
        parent.active = True
        s.execute(
            update(Rating)
            .where(
                Rating.conversation_id.in_(
                    select(Conversation.id).where(Conversation.user_id == _to_uuid(user_id))
                ),
                Rating.prompt_version_id == active.id,
                Rating.consumed_for_evolution.is_(False),
            )
            .values(consumed_for_evolution=True)
        )
        s.flush()
        return str(parent.id)


def load_messages(conversation_id: str, user_id: str | uuid.UUID, *, limit: int = 200) -> list[dict[str, Any]]:
    cid = _to_uuid(conversation_id)
    with session_scope() as s:
        _owned_conversation(s, cid, user_id)
        rows = s.scalars(
            select(Message)
            .where(Message.conversation_id == cid)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
        return [
            {
                "role": m.role,
                "content": m.content,
                "language": m.language,
                "english_expression": m.english_expression,
                "better_expression": m.better_expression,
            }
            for m in reversed(rows)
        ]
