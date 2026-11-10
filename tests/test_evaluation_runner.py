import pytest
import json

from app.evaluate import Benchmark, ExpressionScores, items_for, read, review_flags, score_values, select_items
from app.graph_db import recall


def test_generator_inputs_do_not_contain_gold_fields_or_checkpoints():
    for suite, count in [('memory', 90), ('dialogue', 12), ('expression', 60)]:
        items = items_for(suite)
        assert len(items) == count
        for item in items:
            assert not {'expected_output', 'expected_facts', 'evidence', 'required_behaviors', 'acceptable_rewrites'} & set(item['input'])
            if suite == 'dialogue':
                assert all(set(turn) == {'id', 'language', 'user_text'} for turn in item['input']['turns'])
        pilot = select_items(items, limit=6)
        assert len(pilot) == 6
        assert len({item['metadata']['category'] for item in pilot}) >= 5


def test_case_selection_rejects_unknown_ids():
    items = items_for('expression')
    assert len(select_items(items, cases='ko-01,en-01')) == 2
    with pytest.raises(ValueError):
        select_items(items, cases='not-a-case')


def expression_verdict():
    return {'status': 'ok', 'scores': {'meaning_preserved':1, 'grammar_correct':1, 'naturalness':2, 'register_fit':1, 'unnecessary_correction':0, 'explanation_correct':1}, 'reason':'test'}


def test_missing_correction_context_is_not_credited_to_the_model():
    item = next(item for item in items_for('expression') if item['metadata']['case_id'] == 'en-13')
    verdict = expression_verdict()
    scores = score_values('expression', verdict, item['metadata'], item['input'], item['expected_output'])
    assert scores['meaning_preserved'] is None
    assert scores['register_fit'] is None
    assert scores['expression_success'] is None
    assert verdict['scores']['meaning_preserved'] == 1


def test_fluent_meaning_change_fails_expression_success():
    item = next(item for item in items_for('expression') if item['metadata']['case_id'] == 'en-01')
    verdict = expression_verdict()
    verdict['scores']['meaning_preserved'] = 0
    scores = score_values('expression', verdict, item['metadata'], item['input'], item['expected_output'])
    assert scores['naturalness'] == 2
    assert scores['expression_success'] == 0


@pytest.mark.parametrize('bad', [True, 3, '1'])
def test_invalid_score_types_and_ranges_are_rejected(bad):
    item = items_for('expression')[0]
    verdict = expression_verdict()
    verdict['scores']['grammar_correct'] = bad
    verdict['scores']['explanation_correct'] = None
    with pytest.raises(ValueError):
        score_values('expression', verdict, item['metadata'], item['input'], item['expected_output'])


def test_hallucination_empty_gold_does_not_receive_correctness_score():
    item = next(item for item in items_for('memory') if item['metadata']['case_id'] == 's1-ha01')
    verdict = {'correctness':2, 'faithfulness':2, 'hallucination':0, 'clarification_success':1}
    with pytest.raises(ValueError):
        score_values('memory', verdict, item['metadata'], item['input'], item['expected_output'])
    verdict['correctness'] = None
    assert score_values('memory', verdict, item['metadata'], item['input'], item['expected_output'])['correctness'] is None


def test_graph_failure_can_be_distinguished_from_no_results(monkeypatch):
    monkeypatch.setattr(recall, 'ensure_schema', lambda: False)
    assert recall.recall_related('user', ['topic']) == []
    with pytest.raises(RuntimeError):
        recall.recall_related('user', ['topic'], raise_errors=True)


def test_judge_schema_requires_every_expression_metric():
    assert set(ExpressionScores.model_json_schema()['required']) == set(expression_verdict()['scores'])


def test_calibration_labels_and_future_checkpoints_are_not_in_model_inputs():
    benchmark = Benchmark.__new__(Benchmark)
    benchmark.manifest = read('manifest.json')
    benchmark.sources = {read(path)['set_id']:read(path) for path in benchmark.manifest['suites']['memory']['source_files']}
    items = benchmark.calibration_items()
    assert len(items) == 12
    for item in items:
        assert 'calibration_expected' not in item['input']
        assert 'calibration_expected' not in item['expected_output']
        if item['metadata']['suite'] == 'dialogue':
            assert len(item['input']['turns']) == len(item['expected_output']['turns'])


def test_judge_credit_without_reference_wording_is_flagged_for_review_only():
    expected = {'expected_facts':[{'ko':'integration', 'en':'integration'}]}
    output = {'turns':[{'reply':'It is the standard path through the process.'}]}
    scores = {'correctness':1}
    assert review_flags('memory',output,expected,scores)['review_required'] == 1
    assert scores['correctness'] == 1
    output['turns'][0]['reply'] = 'The default branch is integration.'
    assert review_flags('memory',output,expected,scores)['review_required'] == 0


def test_question_in_scripted_goodbye_flags_conflicting_flow_score():
    output = {'turns':[{'reply':'Take care! What will you do tomorrow?'}]}
    flags = review_flags('dialogue',output,{}, {'dialogue_flow':2})
    assert flags == {'review_required':1, 'closing_question_mark':1}
    output['turns'][0]['reply'] = 'Take care!'
    assert review_flags('dialogue',output,{}, {'dialogue_flow':2})['closing_question_mark'] == 0


def test_english_only_explanation_is_reviewed_without_replacing_the_judge_grade():
    scores = {'explanation_correct':1}
    output = {'candidate':{'suggestion':'I really like it.','note':'Use really before the verb.'}}
    flags = review_flags('expression',output,{},scores)
    assert flags == {'review_required':1,'explanation_korean_present':0}
    assert scores['explanation_correct'] == 1
    output['candidate']['note'] = '동사를 강조할 때 really를 써요.'
    assert review_flags('expression',output,{},scores)['explanation_korean_present'] == 1


def test_replay_preserves_candidate_and_provenance_without_model_or_graph(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    original = {'run_id':'old-run','dataset_version':'2.0','model':'original-model','prompt_refs':{'test':{'version':1}}}
    (source/'run.json').write_text(json.dumps(original))
    output = {'candidate':{'english_expression':'Saved output'},'context_provided':True,'model_visible_input':[]}
    (source/'expression.jsonl').write_text(json.dumps({'stage':'generation','case_id':'ko-01','output':output})+'\n')
    benchmark = Benchmark.__new__(Benchmark)
    benchmark.manifest = read('manifest.json')
    benchmark.report_dir = tmp_path/'new'
    benchmark.report_dir.mkdir()
    benchmark.metadata = {}
    benchmark.replay_outputs = {}
    benchmark.load_replay(source,['expression'])
    item = next(i for i in items_for('expression') if i['metadata']['case_id']=='ko-01')
    assert benchmark.task(item=item) == output
    assert benchmark.metadata['generation_source'] == original
    assert json.loads((source/'run.json').read_text()) == original
    missing = next(i for i in items_for('expression') if i['metadata']['case_id']=='ko-02')
    with pytest.raises(ValueError,match='missing generated outputs'):
        benchmark.run('expression',[missing])
