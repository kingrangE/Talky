import pytest

from app.agents.nodes import better_expr, en_reply, ko_coach


@pytest.mark.parametrize('module,name', [
    (en_reply, 'talky/en-reply'),
    (ko_coach, 'talky/ko-coach'),
    (better_expr, 'talky/better-expression'),
])
def test_native_wording_applies_to_older_prompts_and_korean_context(module, name):
    text = '친구들과 소주를 마셔요. 안주로 치킨을 먹어요.'
    history = [{'role':'user','content':'I like drinking soju with friends.'},
               {'role':'assistant','content':'What is your favorite anju?'}]
    messages = module._build_messages({
        'system_prompt':'A friendly companion for a Korean learner.',
        'common_prompt_texts':{name:'Use familiar Korean words when discussing Korean life.'},
        'retrieved_memory':[{'text':'The user mentioned anju and soju.'}],
        'history':history, 'user_text':text,
    })
    instruction = messages[0].content
    assert 'understood without knowing Korean' in instruction
    assert 'food to have with drinks' in instruction
    assert 'dinner with coworkers' in instruction
    assert 'Keep specific names such as soju and kimchi' in instruction
    assert 'Korean explanations and translations remain in Korean' in instruction
    assert instruction.index('Use familiar Korean words') < instruction.index('understood without knowing Korean')
    assert messages[-1].content == text
    assert any(message.content == history[-1]['content'] for message in messages)


@pytest.mark.parametrize('module,name,text', [
    (en_reply, 'talky/en-reply', "Next time I go to Singapore, I'd like to visit the zoo."),
    (ko_coach, 'talky/ko-coach', '다음에 싱가포르에 가면 동물원에 가고 싶어요.'),
])
def test_reply_follows_the_learners_interest_before_offering_planning_options(module, name, text):
    messages = module._build_messages({
        'system_prompt': 'A friendly companion for a Korean learner.',
        'common_prompt_texts': {name: 'Offer an easy choice to continue the conversation.'},
        'user_text': text,
    })
    instruction = messages[0].content
    assert instruction.index('Offer an easy choice') < instruction.index('ask one easy question about what appeals to them')
    assert 'timing, logistics, or planning choices only if the learner asks about them' in instruction
    assert 'asks for practical advice or to compare options, answer that request directly' in instruction
    assert messages[-1].content == text
