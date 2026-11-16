"""Anonymous exam endpoints retain the existing quotas and signed recovery scope."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.mock_exam import repository as repo
from app.mock_exam.content import list_approved_sets, load_exam_set
from app.mock_exam.privacy import sign_recovery_token, verify_recovery_token, visitor_fingerprint
from app.mock_exam.service import publish_timeout_report, retry_report, submit_response
from app.settings import get_settings
from app.errors import failure_message, UserFacingError

router = APIRouter(prefix='/api/exam')
COOKIE = 'talky_exam'


def session_for(request):
    try:
        sid = str(UUID(verify_recovery_token(request.cookies.get(COOKIE, ''))))
    except ValueError:
        raise HTTPException(404, '복구할 시험이 없거나 복구 링크가 만료되었습니다.') from None
    session = repo.get_session(sid)
    if not session:
        raise HTTPException(404, '시험 데이터의 보관 기간이 지났습니다.')
    if session['status'] == 'active':
        elapsed = (datetime.now(timezone.utc) - session['updated_at']).total_seconds()
        if elapsed > get_settings().MOCK_EXAM_RECOVERY_TTL_MINUTES * 60 and not any(
                row['status'] in {'queued', 'processing'} for row in repo.list_responses(sid)):
            raise HTTPException(410, '중단된 시험의 10분 복구 시간이 지났습니다.')
    return session


@router.get('/quota')
def quota(request: Request):
    return repo.quota_status(visitor_fingerprint(request.client.host if request.client else None, 'web'))


class Consent(BaseModel):
    consent: bool


@router.post('/session', status_code=201)
def start(body: Consent, request: Request, response: Response):
    if not body.consent:
        raise ValueError('개인정보 처리와 72시간 보관에 동의해 주세요.')
    exams = list_approved_sets()
    if not exams:
        raise HTTPException(503, '준비된 시험이 없습니다.')
    exam = exams[0]
    created = repo.create_session(visitor_hash=visitor_fingerprint(request.client.host if request.client else None, 'web'),
                                  exam_set_id=exam.id, exam_set_version=exam.version)
    response.set_cookie(COOKIE, sign_recovery_token(created['id'], ttl_seconds=get_settings().MOCK_EXAM_RESULT_TTL_HOURS * 3600),
                        httponly=True, secure=request.url.scheme == 'https', samesite='strict', max_age=259200)
    return {'ok': True}


class Recovery(BaseModel):
    token: str = Field(min_length=1, max_length=2048)


@router.post('/recover')
def recover(body: Recovery, request: Request, response: Response):
    try:
        sid = str(UUID(verify_recovery_token(body.token)))
    except ValueError:
        raise ValueError('복구 링크가 올바르지 않거나 만료되었습니다.') from None
    if not repo.get_session(sid):
        raise HTTPException(404, '시험 데이터의 보관 기간이 지났습니다.')
    response.set_cookie(COOKIE, body.token, httponly=True, secure=request.url.scheme == 'https', samesite='strict')
    return {'ok': True}


@router.get('/session')
def current(request: Request):
    session = session_for(request)
    exam = load_exam_set(session['exam_set_id'])
    report = repo.get_report(session['id'])
    if session['status'] == 'scoring' and not report:
        if (datetime.now(timezone.utc) - session['updated_at']).total_seconds() >= get_settings().MOCK_EXAM_SCORING_TIMEOUT_SECONDS:
            publish_timeout_report(session['id'])
            report = repo.get_report(session['id'])
    question = exam.questions[session['current_question'] - 1]
    public_question = question.model_dump(exclude={'rubric_anchors', 'expected_facts', 'narrator_audio_path'})
    asset = next((a for a in exam.assets if a.asset_id == question.asset_id), None)
    responses = repo.list_responses(session['id'])
    failed = next((r['error_code'] for r in responses if r['status'] == 'failed'), None)
    return {**session, 'title': exam.title, 'question': public_question, 'asset': asset.model_dump() if asset else None,
            'error_message': failure_message(failed or (report or {}).get('payload', {}).get('error_code')) if session['status'] == 'failed' else None,
            'responses': [{k: r[k] for k in ('question_number', 'status', 'transcript', 'attempts')} | {
                'error_message': failure_message(r['error_code']) if r['status'] in {'failed', 'technical_error'} else None} for r in responses],
            'report': report, 'recovery_token': sign_recovery_token(session['id'], ttl_seconds=max(1, int((session['expires_at'] - datetime.now(timezone.utc)).total_seconds())))}


@router.get('/narration')
def narration(request: Request):
    from app.mock_exam.narration import narration_for
    session = session_for(request)
    exam = load_exam_set(session['exam_set_id'])
    audio = narration_for(exam, exam.questions[session['current_question'] - 1])
    if not audio:
        raise HTTPException(503, '안내 음성을 사용할 수 없어 화면의 문항으로 진행합니다.')
    return Response(audio, media_type='audio/wav')


@router.put('/response/{number}')
async def answer(number: int, request: Request):
    session = await run_in_threadpool(session_for, request)
    if not 1 <= number <= 11:
        raise ValueError('문항 번호가 올바르지 않습니다.')
    data = await request.body()
    if not data:
        raise UserFacingError('audio_empty')
    existing = await run_in_threadpool(repo.list_responses, session['id'])
    # A lost acknowledgement must not create a second response or require re-recording.
    if any(r['question_number'] == number and r['status'] != 'technical_error' for r in existing):
        return {'ok': True}
    await run_in_threadpool(submit_response, session_id=session['id'], question_number=number,
                           audio_bytes=data, client_metrics={})
    return {'ok': True}


@router.post('/retry-report')
def retry(request: Request):
    retry_report(session_for(request)['id'])
    return {'ok': True}


class Calibration(BaseModel):
    score: int = Field(ge=0, le=200, multiple_of=10)
    month: str | None = Field(default=None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$')


@router.post('/calibration')
def calibration(body: Calibration, request: Request):
    report = repo.get_report(session_for(request)['id'])
    if not report or report['status'] not in {'partial', 'completed'}:
        raise ValueError('보고서가 완성된 후 제출해 주세요.')
    repo.save_calibration_sample(official_score=body.score, official_exam_month=body.month, report=report)
    return {'ok': True}
