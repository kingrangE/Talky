import pytest
from app.ui.submission import submit_pending


def test_committed_but_lost_ack_reuses_identity_even_after_completion():
    state, stored, ids = {}, {}, []
    def submit(**kw):
        rid = kw['request_id']
        ids.append(rid)
        if rid not in stored:
            stored[rid] = {'id': rid, 'status': 'completed'}
            raise OSError('lost ack after commit')
        return stored[rid]
    intent = dict(conversation_id='c', user_id='u', user_text='Hello')
    with pytest.raises(OSError):
        submit_pending(state, submit, **intent)
    with pytest.raises(ValueError):
        submit_pending(state, submit, **{**intent, 'user_text': 'different'})
    assert submit_pending(state, submit, **intent)['status'] == 'completed'
    assert ids[0] == ids[1] and len(stored) == 1
    with pytest.raises(OSError):
        submit_pending(state, submit, **intent)
    assert ids[-1] != ids[0]


def test_restored_receipt_is_used_before_any_network_submission():
    state={'resume_request_id':'durable-id'}
    receipts=[]
    def submit(**kw):
        assert receipts == [('durable-id','c')]
        assert kw['request_id']=='durable-id'
        return {'id':'durable-id'}
    submit_pending(state,submit,conversation_id='c',user_id='u',user_text='Hello',
                   before_submit=lambda rid,cid:receipts.append((rid,cid)))
    assert state['pending_turn_id']=='durable-id'
