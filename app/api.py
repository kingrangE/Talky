"""Same-origin web API; workers continue to own durable inference jobs."""
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import UUID
import logging
import re
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app import auth, conversation
from app.db import repo
from app.db.models import ConversationTurn, Message, User
from app.db.postgres import get_engine, session_scope
from app.mock_exam.privacy import audio_store, sign_recovery_token, verify_recovery_token
from app.settings import get_settings
from app.errors import UserFacingError, error_code, failure_message, input_message

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
COOKIE = 'talky_session'
log = logging.getLogger(__name__)
_index = Path(__file__).resolve().parents[1] / 'frontend/dist/index.html'
_asset = re.search(r'/assets/(index-[^"/]+\.js)', _index.read_text()) if _index.exists() else None
WEB_BUILD = _asset.group(1) if _asset else ''


@app.middleware('http')
async def boundary(request: Request, call_next):
    if request.url.path.startswith('/api/'):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if ((origin and origin != f'{request.url.scheme}://{request.headers.get("host")}')
                    or request.headers.get('sec-fetch-site') == 'cross-site'):
                return JSONResponse({'detail': '다른 사이트에서 보낸 요청은 허용하지 않습니다.'}, status_code=403)
            receive_started = monotonic()
            data = bytearray()
            async for part in request.stream():
                data.extend(part)
                if len(data) > get_settings().MAX_AUDIO_BYTES + 32768:
                    return JSONResponse({'detail': '업로드가 너무 큽니다.'}, status_code=413)
            request._body = bytes(data)
            request.state.body_receive_ms = (monotonic() - receive_started) * 1000
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if WEB_BUILD:
            response.headers['X-Talky-Web-Build'] = WEB_BUILD
        return response
    return await call_next(request)


@app.exception_handler(ValueError)
async def invalid(_request, exc):
    return JSONResponse({'detail': input_message(exc), 'error_code': error_code(exc)}, status_code=400)


@app.exception_handler(PermissionError)
async def forbidden(_request, exc):
    if str(exc) == 'mock exam quota exceeded':
        return JSONResponse({'detail': '현재 이용 한도에 도달했습니다. 잠시 후 다시 확인해 주세요.'}, status_code=429)
    return JSONResponse({'detail': failure_message(exc), 'error_code': error_code(exc)}, status_code=503)


@app.exception_handler(RequestValidationError)
async def malformed(_request, _exc):
    return JSONResponse({'detail': '입력 형식과 길이를 확인해 주세요.'}, status_code=422)


@app.exception_handler(Exception)
async def unavailable(_request, exc):
    log.error('API request failed (%s)', type(exc).__name__)
    return JSONResponse({'detail': failure_message(exc), 'error_code': error_code(exc)}, status_code=503)


def user_id(request: Request) -> str:
    try:
        identity = verify_recovery_token(request.cookies.get(COOKIE, ''))
        purpose, value = identity.split(':', 1)
        if purpose != 'account':
            raise ValueError('wrong token purpose')
        uid = UUID(value)
        with session_scope() as session:
            if session.get(User, uid) is None:
                raise ValueError('unknown user')
        return str(uid)
    except ValueError:
        raise HTTPException(401, '다시 로그인해 주세요.') from None


class Credentials(BaseModel):
    login_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@app.post('/api/auth/register', status_code=201)
def register(body: Credentials):
    auth.register_user(body.login_name, body.password)
    return {'ok': True}


@app.post('/api/auth/login')
def login(body: Credentials, request: Request, response: Response):
    uid = auth.authenticate(body.login_name, body.password)
    if not uid:
        raise HTTPException(401, '아이디와 비밀번호를 확인해 주세요. 반복 실패 시 15분 동안 잠깁니다.')
    response.set_cookie(COOKIE, sign_recovery_token(f'account:{uid}', ttl_seconds=43200),
                        max_age=43200, httponly=True, secure=request.url.scheme == 'https', samesite='strict')
    return {'id': uid}


@app.post('/api/auth/logout')
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {'ok': True}


@app.get('/api/auth/me')
def me(uid: str = Depends(user_id)):
    with session_scope() as session:
        return {'id': uid, 'name': session.get(User, UUID(uid)).display_name}


@app.get('/api/health')
def health():
    from app.schema_contract import check_schema
    with get_engine().connect() as connection:
        check_schema(connection)
    return {'status': 'ok'}


@app.get('/api/conversations')
def conversations(offset: int = 0, uid: str = Depends(user_id)):
    return repo.list_conversations(uid, offset=max(0, offset))


class Title(BaseModel):
    title: str = Field(min_length=1, max_length=100)


@app.post('/api/conversations', status_code=201)
def create(body: Title | None = None, uid: str = Depends(user_id)):
    if body is None:
        return {'id': repo.create_conversation(user_id=uid)}
    title = body.title.strip()
    if not title:
        raise ValueError('대화 제목을 입력해 주세요.')
    return {'id': repo.create_conversation(title=title, user_id=uid)}


def public_turn(turn):
    return {key: turn[key] for key in ('id', 'conversation_id', 'status', 'input_text', 'result', 'error_code')} | {
        'has_input_audio': bool(turn['input_audio_path']),
        'has_output_audio': bool(turn['output_audio_path']) and turn['result'].get('speech', {}).get('status', 'ready') == 'ready',
        'has_audio_stream': bool(turn['output_audio_path']) and (
            turn['result'].get('speech', {}).get('status') == 'ready' or (
                turn['status'] == 'processing' and turn['result'].get('speech', {}).get('status') == 'generating'
                and turn['result']['speech'].get('attempt') == turn['attempt'])),
        'starter_topic': turn.get('starter_topic'),
        'error_message': failure_message(turn['error_code']) if turn['status'] == 'failed' else None}


@app.get('/api/conversations/{cid}')
def detail(cid: UUID, uid: str = Depends(user_id)):
    with session_scope() as session:
        # Read messages and job receipts from one snapshot while a worker commits.
        session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        conv = repo._owned_conversation(session, cid, uid)
        messages = session.scalars(select(Message).where(Message.conversation_id == cid)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(200)).all()
        turns = session.scalars(select(ConversationTurn).where(ConversationTurn.conversation_id == cid)
            .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc()).limit(200)).all()
        return {'id': str(cid), 'title': conv.title, 'ended_at': conv.ended_at,
                'messages': [{'id': str(m.id), 'role': m.role, 'content': m.content,
                    'english_expression': m.english_expression, 'better_expression': m.better_expression,
                    'korean_translation': m.korean_translation} for m in reversed(messages)],
                'turns': [public_turn(conversation._record(t)) for t in reversed(turns)],
                'report': repo._read_report(session, cid), 'rating': repo._read_rating(session, cid)}


@app.post('/api/messages/{mid}/translation')
def reply_translation(mid: UUID, uid: str = Depends(user_id)):
    from app.reply_translation import translate_reply
    try:
        return {'translation': translate_reply(mid, UUID(uid))}
    except LookupError:
        raise HTTPException(404, '번역할 답변을 찾을 수 없습니다.') from None


@app.patch('/api/conversations/{cid}')
def rename(cid: UUID, body: Title, uid: str = Depends(user_id)):
    if not body.title.strip():
        raise ValueError('대화 제목을 입력해 주세요.')
    repo.update_conversation_title(str(cid), body.title.strip(), uid)
    return {'ok': True}


@app.delete('/api/conversations/{cid}')
def delete(cid: UUID, uid: str = Depends(user_id)):
    repo.delete_conversation(str(cid), uid)
    return {'ok': True}


class TextTurn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Starter(BaseModel):
    topic: Literal['daily', 'travel', 'food', 'hobbies', 'work', 'random']


@app.put('/api/conversations/{cid}/turns/{rid}/starter')
def start_topic(cid: UUID, rid: UUID, body: Starter, uid: str = Depends(user_id)):
    return public_turn(conversation.submit_turn(request_id=str(rid), conversation_id=str(cid),
                        user_id=uid, starter_topic=body.topic))


@app.put('/api/conversations/{cid}/turns/{rid}')
def send_text(cid: UUID, rid: UUID, body: TextTurn, uid: str = Depends(user_id)):
    return public_turn(conversation.submit_turn(request_id=str(rid), conversation_id=str(cid), user_id=uid, user_text=body.text))


@app.put('/api/conversations/{cid}/turns/{rid}/voice')
async def send_voice(cid: UUID, rid: UUID, request: Request, response: Response, uid: str = Depends(user_id)):
    from starlette.concurrency import run_in_threadpool
    data = await request.body()
    if not data:
        raise UserFacingError('audio_empty')
    started = monotonic()
    turn = await run_in_threadpool(conversation.submit_turn, request_id=str(rid), conversation_id=str(cid), user_id=uid, audio_bytes=data)
    response.headers['Server-Timing'] = (
        f'body;dur={request.state.body_receive_ms:.2f}, '
        f'accept;dur={(monotonic() - started) * 1000:.2f}'
    )
    return public_turn(turn)


@app.get('/api/turns/{rid}')
def turn_status(rid: UUID, uid: str = Depends(user_id)):
    try:
        turn = conversation.get_turn(str(rid), uid)
    except ValueError:
        raise HTTPException(404, '요청을 찾을 수 없습니다.') from None
    return public_turn(turn)


@app.get('/api/turns/{rid}/audio-stream')
async def turn_audio_stream(rid: UUID, uid: str = Depends(user_id)):
    from starlette.concurrency import run_in_threadpool
    from app.conversation_audio import stream
    turn = await run_in_threadpool(conversation.get_turn, str(rid), uid)
    if not public_turn(turn)['has_audio_stream']:
        raise HTTPException(404, '음성을 사용할 수 없거나 보관 기간이 지났습니다.')
    chunks = stream(str(rid), uid, turn['attempt'])
    try:
        first = await anext(chunks)
    except Exception:
        await chunks.aclose()
        raise HTTPException(503, '음성을 불러오지 못했어요. 자막으로 확인해 주세요.') from None
    async def deliver():
        try:
            yield first
            async for chunk in chunks:
                yield chunk
        finally:
            await chunks.aclose()
    return StreamingResponse(deliver(), media_type='audio/pcm', headers={
        'X-Audio-Sample-Rate': '24000', 'X-Accel-Buffering': 'no'})


@app.get('/api/turns/{rid}/audio')
def turn_audio(rid: UUID, kind: str = 'output', uid: str = Depends(user_id)):
    if kind not in {'input', 'output'}:
        raise HTTPException(404)
    turn = conversation.get_turn(str(rid), uid)
    path = turn[f'{kind}_audio_path']
    if kind == 'output' and not public_turn(turn)['has_output_audio']:
        path = None
    if not path:
        raise HTTPException(404, '음성을 사용할 수 없거나 보관 기간이 지났습니다.')
    try:
        return Response(audio_store().load(path), media_type='audio/wav' if kind == 'output' else 'application/octet-stream')
    except (OSError, ValueError):
        raise HTTPException(404, '음성을 사용할 수 없거나 보관 기간이 지났습니다.') from None


@app.post('/api/conversations/{cid}/report')
def finish(cid: UUID, uid: str = Depends(user_id)):
    from app.agents.ending_graph import build_ending_graph
    with session_scope() as session:
        conv = repo._owned_conversation(session, cid, uid, lock=True)
        if session.scalar(select(ConversationTurn.id).where(ConversationTurn.conversation_id == cid,
                ConversationTurn.status.in_(['queued', 'processing'])).limit(1)):
            raise ValueError('진행 중인 응답이 끝나면 대화를 마칠 수 있습니다.')
        conv.ended_at = conv.ended_at or datetime.now(timezone.utc)
    if repo.get_report(str(cid), uid) is None:
        build_ending_graph().invoke({'conversation_id': str(cid), 'user_id': uid})
    return repo.get_report(str(cid), uid)


class Rating(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str = Field(default='', max_length=2000)


@app.put('/api/conversations/{cid}/rating')
def rating(cid: UUID, body: Rating, uid: str = Depends(user_id)):
    repo.save_rating(conversation_id=str(cid), user_id=uid, prompt_version_id=None,
                     stars=body.stars, comment=body.comment.strip() or None)
    from app.agents.evolve_graph import run_evolve_if_needed
    try:
        run_evolve_if_needed(uid)
    except Exception as exc:
        log.warning('Rating saved; evolution deferred (%s)', type(exc).__name__)
    return {'ok': True}


# Register API routes before the static frontend mount.
from app.exam_api import router as exam_router
app.include_router(exam_router)
dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
