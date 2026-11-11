import json
from pathlib import Path
import pytest
from app.agents.nodes import memory


@pytest.mark.parametrize('text', ['Hello!', ' THANK YOU!! ', '안녕하세요!', '고마워요.', 'See you later!'])
def test_social_only_skips_all_expensive_read_operations(monkeypatch,text):
    def forbidden(*_a,**_kw):
        pytest.fail('social gate called a model or database')
    for name in ['_extract_topics','recall_related','active_conversation_ids']:
        monkeypatch.setattr(memory,name,forbidden)
    result=memory.memory_recall_node({'user_id':'u','user_text':text,'history':[],
                                     'retrieved_memory':[{'text':'stale'}],'memory_topics':['stale']})
    assert result=={'retrieved_memory':[],'memory_topics':[],'memory_recall_decision':'social_only'}


@pytest.mark.parametrize('text', [
    'Hello! Do you remember my favorite place?', '내가 좋아하는 그거 있잖아!',
    '오늘도 야근했어', 'What did I tell you before?', 'Yes', 'Okay',
    'I like climbing', '고마워요. 지난번 카페가 어디였죠?', 'Tell me about it',
])
def test_ambiguous_or_personal_utterances_keep_retrieval(text):
    assert memory.recall_decision(text,[])[0]


def test_recent_context_overrides_social_skip():
    assert memory.recall_decision('Hello',[{'role':'assistant','content':'What was the title you mentioned?'}])==(True,'context_dependency')


def test_all_existing_memory_questions_still_request_retrieval():
    count=0
    for folder in ['single_hop','multi_hop','hallucination']:
        for path in (Path('data/test')/folder).glob('*.json'):
            for case in json.loads(path.read_text())['questions']:
                assert memory.recall_decision(case['question'],[])[0],case['id']
                count+=1
    assert count==90


def test_social_fast_path_does_not_reserve_a_model_timeout(monkeypatch):
    from app.runtime import job_budget
    from app.telemetry import timed_stage
    operation=timed_stage('memory_recall',memory.memory_recall_node)
    with job_budget(2):
        assert operation({'user_id':'u','user_text':'Hello!'})['memory_recall_decision']=='social_only'
        with pytest.raises(TimeoutError):
            operation({'user_id':'u','user_text':'What was my favorite cafe?'})
