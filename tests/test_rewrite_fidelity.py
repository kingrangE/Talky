import pytest

from app.agents.nodes import better_expr, ko_coach

TRANSCRIPT = ('I made LLM program like TOKII. TOKII is English conversation running service. '
              'And I studied LLM skills like language, language series or something.')


@pytest.mark.parametrize('module,name', [(better_expr,'talky/better-expression'), (ko_coach,'talky/ko-coach')])
@pytest.mark.parametrize('speech', [False, True])
def test_rewrite_distinguishes_unclear_content_from_contextual_speech_repair(module, name, speech):
    state = {'system_prompt':'English conversation companion.', 'user_text':TRANSCRIPT,
        'common_prompt_texts':{name:'Improve the learner\'s phrasing and add impressive technical examples.'},
        'history':[{'role':'assistant','content':'Perhaps you studied RAG or fine-tuning?'}]}
    if speech:
        state['audio_bytes'] = b'fixture'
    messages = module._build_messages(state)
    instruction = messages[0].content
    assert 'Content fidelity takes priority over naturalness' in instruction
    assert 'like X does not mean called X' in instruction
    assert 'Never turn studied into have been studying' in instruction
    assert 'Do not guess technical names' in instruction
    assert 'The input is a speech transcript' in instruction if speech else 'The input is typed text' in instruction
    assert messages[-1].content == TRANSCRIPT
    assert state['user_text'] == TRANSCRIPT
