import copy

import pytest

from app.agents.nodes import better_expr, ko_coach
from app.llm import rewrite_protection as protection

SOURCE = 'I made LLM program like TOKII. TOKII is English conversation running service. And I studied LLM skills like language, language series or something.'
TERMS = ['LLM', 'TOKII', 'language, language series or something']


def prepare(monkeypatch, state, names=TERMS, repairs=()):
    plan = protection.SourceProtection(names=names, speech_repairs=list(repairs))
    monkeypatch.setattr(protection, 'get_structured_llm', lambda *_a, **_kw: object())
    def invoke(*_, capture_content):
        assert capture_content is False
        return plan

    monkeypatch.setattr(protection, 'invoke_with_prompt', invoke)
    return protection.prepare_rewrite(state)


def correction(text):
    return better_expr.BetterExpressionOutput(suggestion=text, note={
        'translation': '전체 문장 해석.',
        'explanations': [{'pattern': 'like', 'explanation': '비슷하다는 뜻이에요.'}]})


def test_literal_names_survive_editor_without_revealing_their_spelling(monkeypatch):
    state = {'user_text': SOURCE, 'audio_bytes': b'fixture'}
    original = copy.deepcopy(state)
    prepared = prepare(monkeypatch, state)
    for name in TERMS:
        assert name not in prepared.state['user_text']
    draft = correction(prepared.state['user_text'].replace('I made ', 'I built '))
    restored = protection.restore_rewrite(draft, prepared)
    assert restored.suggestion == SOURCE.replace('I made ', 'I built ')
    assert state == original


@pytest.mark.parametrize('text', ['LangGraph', ' ', 'LangSeries'])
def test_extractor_cannot_invent_canonical_names(monkeypatch, text):
    with pytest.raises(ValueError, match='original substring'):
        prepare(monkeypatch, {'user_text': SOURCE}, [text])


@pytest.mark.parametrize('replacement', ['', 'RAG', '__TALKY_NAME_999__'])
def test_lost_or_invented_marker_rejects_rewrite(monkeypatch, replacement):
    prepared = prepare(monkeypatch, {'user_text': SOURCE})
    marker = next(iter(prepared.names))
    draft = correction(prepared.state['user_text'].replace(marker, replacement))
    with pytest.raises(ValueError):
        protection.restore_rewrite(draft, prepared)


def test_longest_overlapping_name_restores_exactly(monkeypatch):
    state = {'user_text': 'I studied language and language series.'}
    prepared = prepare(monkeypatch, state, ['language', 'language series'])
    assert protection.restore_rewrite(correction(prepared.state['user_text']), prepared).suggestion == state['user_text']


def test_speech_repair_is_separate_from_literal_names_and_does_not_mutate_source(monkeypatch):
    state = {'user_text': SOURCE, 'audio_bytes': b'fixture'}
    repairs = [{'source': 'running service', 'replacement': 'learning service'},
               {'source': 'language series', 'replacement': 'LangSeries'}]
    prepared = prepare(monkeypatch, state, repairs=repairs)
    restored = protection.restore_rewrite(correction(prepared.state['user_text']), prepared)
    assert 'learning service' in restored.suggestion
    assert 'language, language series or something' in restored.suggestion
    assert 'LangSeries' not in restored.suggestion
    assert state['user_text'] == SOURCE
    assert len(prepared.repairs) == 1
    assert '보정 추정' in protection.repair_notes(prepared)


def test_typed_input_never_applies_speech_repairs(monkeypatch):
    prepared = prepare(monkeypatch, {'user_text': SOURCE}, repairs=[{'source': 'running', 'replacement': 'learning'}])
    assert not prepared.repairs
    assert 'running' in prepared.state['user_text']


@pytest.mark.parametrize('module', [better_expr, ko_coach])
def test_real_nodes_publish_restored_names_without_clarification_gate(monkeypatch, module):
    state = {'user_text': SOURCE, 'system_prompt': 'An English companion.', 'audio_bytes': b'fixture'}
    prepared = prepare(monkeypatch, state, repairs=[{'source': 'running service', 'replacement': 'learning service'}])
    monkeypatch.setattr(module, 'prepare_rewrite', lambda _: prepared)
    monkeypatch.setattr(module, 'get_structured_llm', lambda _: object())
    def invoke(_llm, messages, *_):
        assert messages[-1].content == prepared.state['user_text']
        assert 'Literal name key (for grammar and pronunciation only)' in messages[0].content
        assert 'Mandatory copy counts' in messages[0].content
        assert 'Do not turn repeated named subjects into pronouns' in messages[0].content
        english = messages[-1].content.replace('I made ', 'I built ')
        return correction(english) if module is better_expr else ko_coach.KoCoachOutput(reply='What did you enjoy building?', english_expression=english)
    monkeypatch.setattr(module, 'invoke_with_prompt', invoke)
    result = module.better_expr_node(state) if module is better_expr else module.ko_coach_node(state)
    english = result['better_expression'] if module is better_expr else result['english_expression']
    assert 'language, language series or something' in english
    assert 'learning service' in english
    assert '__TALKY_NAME' not in str(result)
    assert state['user_text'] == SOURCE


def test_failed_name_preparation_does_not_publish_unprotected_correction(monkeypatch):
    def timeout(_):
        raise TimeoutError('source preparation unavailable')
    monkeypatch.setattr(better_expr, 'prepare_rewrite', timeout)
    assert better_expr.better_expr_node({'user_text': SOURCE}) == {'better_expression': None, 'correction_status': 'failed'}


def test_literal_key_or_mixed_markers_are_valid_only_with_exact_original_names(monkeypatch):
    prepared = prepare(monkeypatch, {'user_text': SOURCE})
    assert protection.restore_rewrite(correction(SOURCE), prepared).suggestion == SOURCE
    marker, literal = next(iter(prepared.names.items()))
    mixed = prepared.state['user_text'].replace(marker, literal)
    assert protection.restore_rewrite(correction(mixed), prepared).suggestion == SOURCE
    with pytest.raises(ValueError):
        protection.restore_rewrite(correction(SOURCE.replace('language, language series or something', 'LangGraph and LangSeries')), prepared)
