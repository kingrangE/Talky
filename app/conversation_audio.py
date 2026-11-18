"""Publish encrypted audio prefixes under the conversation worker's existing lease."""
import asyncio
import time
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from app.audio import neural2
from app.db.models import ConversationTurn, PendingAudioDeletion
from app.db.postgres import session_scope
from app.db.repo import _to_uuid
from app.mock_exam.privacy import audio_store
from app.runtime import require_budget, resource_slot
from app.audio.usage_budget import reserve


def owned_attempt(session, state):
    row = session.get(ConversationTurn, _to_uuid(state['request_id']), with_for_update=True)
    if (not row or str(row.user_id) != state['user_id'] or row.status != 'processing'
            or row.attempts != state['turn_attempt'] or not row.lease_until
            or row.lease_until <= datetime.now(timezone.utc)):
        raise ValueError('Stale conversation speech attempt')
    return row


def publish(state, status, wav=None):
    path = audio_store().save(state['request_id'], 1, wav) if wav else None
    accepted = False
    try:
        with session_scope() as session:
            row = owned_attempt(session, state)
            if row.output_audio_path:
                session.merge(PendingAudioDeletion(path=row.output_audio_path))
            row.output_audio_path = path
            row.result = {**(row.result or {}), 'speech': {'status': status, 'attempt': row.attempts}}
        accepted = True
    finally:
        if path and not accepted:
            from app.conversation import _discard
            _discard(path)


def generate(state):
    parts = neural2.segments(state['ai_reply'])
    publish(state, 'generating')
    try:
        with resource_slot('audio'):
            pcm = bytearray()
            started = time.monotonic()
            for index, part in enumerate(parts):
                require_budget(16)
                if time.monotonic() - started > 90:
                    raise TimeoutError('conversation speech deadline')
                # Fence immediately before each paid call as well as before publishing it.
                with session_scope() as session:
                    owned_attempt(session, state)
                reserve(len(part))
                pcm.extend(neural2.synthesize(part))
                if len(pcm) > neural2.RATE * 2 * 120:
                    raise ValueError('conversation speech too long')
                # shortcut: rewrite a bounded 120s prefix per sentence to reuse existing storage/expiry.
                publish(state, 'ready' if index == len(parts) - 1 else 'generating', neural2.encode(pcm))
    except Exception:
        publish(state, 'failed')
        raise


async def stream(request_id, user_id, attempt):
    """Read only persisted prefixes. Disconnecting never starts another synthesis."""
    from app.conversation import get_turn
    offset = 0
    loaded_path = None
    started = time.monotonic()
    while time.monotonic() - started < 120:
        current = await run_in_threadpool(get_turn, request_id, user_id)
        speech = current['result'].get('speech', {})
        if current['attempt'] != attempt or speech.get('status') not in {'generating', 'ready'}:
            raise RuntimeError('conversation speech interrupted')
        if speech['status'] == 'generating' and speech.get('attempt') != attempt:
            raise RuntimeError('conversation speech attempt changed')
        if current['status'] == 'failed':
            raise RuntimeError('conversation worker failed')
        path = current['output_audio_path']
        if path and path != loaded_path:
            try:
                data = neural2.decode(await run_in_threadpool(audio_store().load, path))
            except OSError:
                if speech['status'] == 'ready':
                    raise RuntimeError('conversation speech unavailable') from None
                # A new prefix can replace the snapshot while pending deletion runs.
                await asyncio.sleep(.1)
                continue
            if len(data) < offset:
                raise RuntimeError('conversation speech changed')
            for position in range(offset, len(data), 4800):
                yield data[position:position + 4800]
            offset = len(data)
            loaded_path = path
        if speech['status'] == 'ready':
            if not path:
                raise RuntimeError('conversation speech expired')
            return
        await asyncio.sleep(.1)
    raise TimeoutError('conversation speech stream deadline')
