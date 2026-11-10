from app.agents.nodes import better_expr
from app.runtime import bounded_messages


def test_correction_receives_context_but_keeps_latest_utterance_as_target():
    context = 'I am requesting a file from my manager, so I need a polite tone.'
    messages = bounded_messages(better_expr._build_messages({
        'user_text':'Give me the file.',
        'history':[{'role':'user','content':context},{'role':'assistant','content':'What would you like to say?'}],
    }))
    assert any(message.content == context for message in messages)
    assert messages[-1].type == 'human'
    assert messages[-1].content == 'Give me the file.'
    assert [message.type for message in messages] == ['system','human','ai','human']
