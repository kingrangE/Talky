"""Durable conversation requests; the UI submits and workers own inference."""
import hashlib
import logging
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update

from app.audio.validation import validate_audio
from app.db.models import ConversationTurn, PendingAudioDeletion
from app.db.postgres import session_scope
from app.db.repo import _owned_conversation, _to_uuid, load_messages
from app.mock_exam.privacy import audio_store
from app.settings import get_settings

log = logging.getLogger('talky.metrics')

QUEUE_GATE = 74201935


def _record(row):
    return {'id':str(row.id),'conversation_id':str(row.conversation_id),'user_id':str(row.user_id),
            'created_at':row.created_at.isoformat(),'status':row.status,'attempt':row.attempts,'input_text':row.input_text,
            'input_audio_path':row.input_audio_path,'output_audio_path':row.output_audio_path,
            'result':row.result or {},'error_code':row.error_code}


def _discard(path):
    if not path:
        return
    try:
        audio_store().delete(path)
    except OSError:
        with session_scope() as session:
            session.merge(PendingAudioDeletion(path=path))


def submit_turn(*, request_id, conversation_id, user_id, user_text=None, audio_bytes=None):
    cfg=get_settings()
    rid,cid,uid=map(_to_uuid,(request_id,conversation_id,user_id))
    if bool(audio_bytes)==bool(user_text and user_text.strip()):
        raise ValueError('텍스트나 음성 중 하나를 보내주세요.')
    if user_text and len(user_text)>cfg.MAX_USER_TEXT_CHARS:
        raise ValueError('입력 문장이 너무 깁니다.')
    with session_scope() as session:
        _owned_conversation(session,cid,uid)
    if audio_bytes:
        validate_audio(audio_bytes)
    digest=hashlib.sha256(b'audio\0'+audio_bytes if audio_bytes else ('text\0'+user_text).encode()).hexdigest()
    uploaded=audio_store().save(str(rid),1,audio_bytes) if audio_bytes else None
    accepted=False
    try:
        now=datetime.now(timezone.utc)
        with session_scope() as session:
            session.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':QUEUE_GATE})
            conv=_owned_conversation(session,cid,uid,lock=True)
            existing=session.get(ConversationTurn,rid)
            if existing:
                if existing.user_id!=uid or existing.conversation_id!=cid or existing.input_digest!=digest:
                    raise ValueError('같은 요청 번호에 다른 내용을 사용할 수 없습니다.')
                return _record(existing)
            if conv.ended_at:
                raise ValueError('종료된 대화입니다.')
            session.execute(update(ConversationTurn).where(ConversationTurn.status=='queued',
                ConversationTurn.queue_deadline<=now).values(status='failed',error_code='queue_expired',updated_at=now))
            if session.scalar(select(ConversationTurn.id).where(ConversationTurn.conversation_id==cid,
                ConversationTurn.status.in_(['queued','processing'])).limit(1)):
                raise ValueError('이 대화의 이전 응답을 처리 중입니다.')
            user_count=session.scalar(select(func.count()).select_from(ConversationTurn).where(
                ConversationTurn.user_id==uid, ConversationTurn.status.in_(['queued','processing'])))
            if user_count>=cfg.CONVERSATION_USER_QUEUE_LIMIT:
                raise ValueError('진행 중인 요청이 많습니다. 기존 응답이 끝난 후 보내주세요.')
            count=session.scalar(select(func.count()).select_from(ConversationTurn).where(
                ConversationTurn.status.in_(['queued','processing'])))
            if count>=cfg.CONVERSATION_QUEUE_LIMIT:
                raise ValueError('요청이 많습니다. 잠시 후 다시 보내주세요.')
            row=ConversationTurn(id=rid,conversation_id=cid,user_id=uid,input_digest=digest,
                input_text=user_text,input_audio_path=uploaded,queue_deadline=now+timedelta(seconds=cfg.CONVERSATION_QUEUE_WAIT_SECONDS),
                audio_expires_at=now+timedelta(hours=cfg.MOCK_EXAM_AUDIO_TTL_HOURS))
            session.add(row)
            session.flush()
            result=_record(row)
        accepted=True
        log.info(json.dumps({'event': 'accepted', 'kind': 'conversation'}))
        return result
    finally:
        if not accepted:
            _discard(uploaded)


def get_turn(request_id,user_id):
    with session_scope() as session:
        row=session.scalars(select(ConversationTurn).where(ConversationTurn.id==_to_uuid(request_id),
            ConversationTurn.user_id==_to_uuid(user_id))).first()
        if row is None:
            raise ValueError('요청을 찾을 수 없습니다.')
        return _record(row)


def active_turn(conversation_id,user_id):
    with session_scope() as session:
        _owned_conversation(session,conversation_id,user_id)
        row=session.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id==_to_uuid(conversation_id),
            ConversationTurn.user_id==_to_uuid(user_id),ConversationTurn.status.in_(['queued','processing']))).first()
        return _record(row) if row else None


def claim_turn():
    cfg=get_settings()
    now=datetime.now(timezone.utc)
    with session_scope() as session:
        expired=session.scalars(select(ConversationTurn).where(ConversationTurn.status=='processing',
            ConversationTurn.lease_until<=now).with_for_update(skip_locked=True)).all()
        for row in expired:
            row.status='failed' if row.attempts>=cfg.MOCK_EXAM_MAX_ATTEMPTS else 'queued'
            row.error_code='worker_interrupted'
            row.lease_until=None
        session.execute(update(ConversationTurn).where(ConversationTurn.status=='queued',
            ConversationTurn.queue_deadline<=now).values(status='failed',error_code='queue_expired',updated_at=now))
        session.flush()
        row=session.scalars(select(ConversationTurn).where(ConversationTurn.status=='queued',
            ConversationTurn.queue_deadline>now).order_by(ConversationTurn.created_at,ConversationTurn.id)
            .with_for_update(skip_locked=True).limit(1)).first()
        if row is None:
            return None
        row.status='processing'
        row.attempts+=1
        row.updated_at=now
        row.lease_until=now+timedelta(seconds=cfg.MOCK_EXAM_JOB_TIMEOUT_SECONDS+30)
        return {**_record(row),'kind':'conversation'}


def fail_turn(job,error_code):
    with session_scope() as session:
        row=session.get(ConversationTurn,_to_uuid(job['id']),with_for_update=True)
        if row and row.status=='processing' and row.attempts==job['attempt']:
            row.status='failed' if row.attempts>=get_settings().MOCK_EXAM_MAX_ATTEMPTS else 'queued'
            row.error_code=error_code[:128]
            row.lease_until=None
            row.updated_at=datetime.now(timezone.utc)


def process_turn(job):
    from app.agents.conversation_graph import build_conversation_graph
    current = get_turn(job['id'], job['user_id'])
    if current['status'] == 'completed':
        return current['result']
    if current['status'] != 'processing' or current['attempt'] != job['attempt']:
        raise ValueError('Stale conversation attempt')
    if current['result']:
        result = current['result']
    else:
        state = {'conversation_id': job['conversation_id'], 'user_id': job['user_id'],
                 'request_id': job['id'], 'turn_attempt': job['attempt'],
                 'history': load_messages(job['conversation_id'], job['user_id'])}
        if job['input_audio_path']:
            state['audio_bytes'] = audio_store().load(job['input_audio_path'])
        else:
            state['user_text'] = job['input_text']
        result = build_conversation_graph().invoke(state)
    path = audio_store().save(job['id'], 1, result['audio_reply']) if result.get('audio_reply') else None
    accepted = False
    try:
        with session_scope() as session:
            row = session.get(ConversationTurn, _to_uuid(job['id']), with_for_update=True)
            if not row or row.status != 'processing' or row.attempts != job['attempt']:
                raise ValueError('Stale conversation attempt')
            if not row.result:
                raise ValueError('empty_conversation_result')
            row.output_audio_path = path
            row.status = 'completed'
            row.lease_until = None
            row.updated_at = datetime.now(timezone.utc)
        accepted = True
    finally:
        if not accepted:
            _discard(path)
    return result
