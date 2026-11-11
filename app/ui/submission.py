"""Keep an uncertain submission's identity for the lifetime of a login session."""
import uuid


def submit_pending(state, submit, *, conversation_id, user_id, **payload):
    pending = state.get('uncertain_submission')
    intent = {'conversation_id': conversation_id, 'user_id': user_id, **payload}
    if pending and pending['intent'] != intent:
        raise ValueError('이전 요청의 접수 여부를 먼저 확인해주세요.')
    if not pending:
        pending = {'request_id': str(uuid.uuid4()), 'intent': intent}
        state['uncertain_submission'] = pending
    try:
        job = submit(request_id=pending['request_id'], **intent)
    except ValueError:
        state.pop('uncertain_submission', None)
        raise
    state['pending_turn_id'] = job['id']
    state.pop('uncertain_submission', None)
    return job
