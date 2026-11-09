"""Run the versioned text benchmark and record item scores in Langfuse."""

from __future__ import annotations

import argparse
import copy
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langfuse import Evaluation, propagate_attributes
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ConfigDict, StrictInt, create_model

from app.agents.nodes import better_expr, en_reply, ko_coach
from app.agents.nodes.memory import TOPIC_PROMPT, _extract_topics
from app.graph_db.ingest import ingest_topics, ingest_turn
from app.graph_db.neo4j_client import get_driver
from app.graph_db.recall import recall_related
from app.llm.factory import get_chat_llm, get_structured_llm
from app.llm.prompt_registry import get_client, invoke_with_prompt, remote_prompt, resolve_reference
from app.runtime import bounded_history, bounded_messages
from app.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/test'


def read(path):
    return json.loads((DATA / path).read_text(encoding='utf-8'))


def items_for(suite):
    manifest = read('manifest.json')
    result = []
    for file in manifest['suites'][suite]['dataset_files']:
        dataset = read(file)
        for case in dataset[manifest['suites'][suite]['case_key']]:
            metadata = {'suite': suite, 'case_id': case['id'], 'dataset_version': manifest['version']}
            if suite == 'expression':
                inputs, expected = case['input'], case['expected_output']
                metadata.update(case['metadata'])
            elif suite == 'memory':
                inputs = {'set_id': dataset['set_id'], 'question': case['question'], 'language': case['language']}
                expected = case
                metadata['category'] = case.get('scenario_type', dataset['question_type'])
            else:
                inputs = {'set_id': case['source_set_id'], 'turns': [{k: turn[k] for k in ('id', 'language', 'user_text')} for turn in case['turns']]}
                expected = case
                metadata['category'] = case['focus']
            result.append({'input': inputs, 'expected_output': expected, 'metadata': metadata})
    return result


def select_items(items, limit=None, cases=None):
    if cases:
        requested = set(cases.split(','))
        selected = [item for item in items if item['metadata']['case_id'] in requested]
        if {item['metadata']['case_id'] for item in selected} != requested:
            raise ValueError('Unknown case ID in --cases')
        return selected
    # Round-robin categories so a small pilot covers more than the first file.
    groups = defaultdict(list)
    for item in items:
        groups[item['metadata']['category']].append(item)
    ordered = []
    while any(groups.values()):
        for group in groups.values():
            if group:
                ordered.append(group.pop(0))
    return ordered[:limit] if limit else ordered


class QualityVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['ok', 'insufficient_context', 'invalid_output']
    scores: dict[str, StrictInt | None]
    reason: str
    issues: list[str] = []


class ExpressionScores(BaseModel):
    model_config = ConfigDict(extra='forbid')
    meaning_preserved: Literal[0, 1] | None
    grammar_correct: Literal[0, 1] | None
    naturalness: Literal[0, 1, 2] | None
    register_fit: Literal[0, 1] | None
    unnecessary_correction: Literal[0, 1] | None
    explanation_correct: Literal[0, 1] | None


class DialogueScores(BaseModel):
    model_config = ConfigDict(extra='forbid')
    responsiveness: Literal[0, 1, 2] | None
    dialogue_flow: Literal[0, 1, 2] | None
    memory_use: Literal[0, 1, 2] | None
    knowledge_update: Literal[0, 1, 2] | None
    error_recovery: Literal[0, 1, 2] | None
    affect_alignment: Literal[0, 1, 2] | None


class ExpressionVerdict(QualityVerdict):
    scores: ExpressionScores


class DialogueVerdict(QualityVerdict):
    scores: DialogueScores


class MemoryVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid')
    correctness: StrictInt | None
    faithfulness: StrictInt
    hallucination: StrictInt
    clarification_success: StrictInt | None
    reason: str
    unsupported_claims: list[str]


def judge_schema(suite, inputs, expected, metadata):
    if suite == 'memory':
        if expected.get('expected_behavior') == 'clarify':
            return create_model('ClarificationVerdict', __base__=MemoryVerdict, correctness=(type(None), ...), clarification_success=(Literal[0, 1], ...))
        return create_model('AnswerVerdict', __base__=MemoryVerdict, correctness=(Literal[0, 1, 2], ...), clarification_success=(type(None), ...))
    if suite == 'dialogue':
        fields = {name:(Literal[0, 1, 2] if name in expected['applicable_metrics'] else type(None), ...) for name in DialogueScores.model_fields}
        scores = create_model('ApplicableDialogueScores', __base__=DialogueScores, **fields)
        return create_model('ApplicableDialogueVerdict', __base__=DialogueVerdict, scores=(scores, ...), status=(Literal['ok'], ...))
    fields = {}
    if inputs['task'] == 'ko_to_en':
        fields['explanation_correct'] = (type(None), ...)
    else:
        fields['explanation_correct'] = (Literal[0, 1], ...)
    fields.update(grammar_correct=(Literal[0, 1], ...), naturalness=(Literal[0, 1, 2], ...), unnecessary_correction=(Literal[0, 1], ...), meaning_preserved=(Literal[0, 1], ...), register_fit=(Literal[0, 1], ...))
    if metadata.get('context_required') and not metadata.get('context_provided', inputs['task'] == 'ko_to_en'):
        fields.update(meaning_preserved=(type(None), ...), register_fit=(type(None), ...))
    scores = create_model('ApplicableExpressionScores', __base__=ExpressionScores, **fields)
    missing_context = metadata.get('context_required') and not metadata.get('context_provided', inputs['task'] == 'ko_to_en')
    return create_model('ApplicableExpressionVerdict', __base__=ExpressionVerdict, scores=(scores, ...), status=(Literal['insufficient_context'] if missing_context else Literal['ok'], ...))


def score_values(suite, verdict, metadata, inputs, expected):
    definitions = read('rubrics.json')['suites'][suite]['metrics']
    scores = dict(verdict['scores']) if suite != 'memory' else {key: verdict[key] for key in definitions}
    if set(scores) != set(definitions):
        raise ValueError('Judge omitted or added a metric')
    for name, value in scores.items():
        if value is not None and (type(value) is not int or value not in definitions[name]['values']):
            raise ValueError(f'Invalid judge score: {name}')
        if value is None and suite == 'memory' and definitions[name]['null_when'] is None:
            raise ValueError(f'Unexpected null score: {name}')
    if suite == 'memory':
        null_key = 'correctness' if expected.get('expected_behavior') == 'clarify' else 'clarification_success'
        if scores[null_key] is not None:
            raise ValueError(f'Expected null for {null_key}')
    elif suite == 'dialogue':
        for name in set(scores) - set(expected['applicable_metrics']):
            if scores[name] is not None:
                raise ValueError('Judge scored an inapplicable dialogue metric')
    elif inputs['task'] == 'ko_to_en' and scores['explanation_correct'] is not None:
        raise ValueError('Korean expression has no explanation field')
    if suite != 'memory' and verdict.get('status') == 'invalid_output':
        scores = {key: None for key in scores}
    if suite == 'expression' and metadata.get('context_required') and not metadata.get('context_provided', inputs['task'] == 'ko_to_en'):
        # The existing correction node receives no history; do not credit hidden context.
        for name in ('meaning_preserved', 'register_fit'):
            scores[name] = None
    if suite == 'expression':
        required = ['meaning_preserved', 'grammar_correct', 'naturalness', 'register_fit', 'unnecessary_correction']
        if inputs['task'] == 'en_correction':
            required.append('explanation_correct')
        scores['expression_success'] = None if any(scores[k] is None for k in required) else int(
            scores['meaning_preserved'] == scores['grammar_correct'] == scores['register_fit'] == 1
            and scores['naturalness'] == 2 and scores['unnecessary_correction'] == 0
            and (inputs['task'] == 'ko_to_en' or scores['explanation_correct'] == 1)
        )
    return scores


def serialize_messages(messages):
    return [{'role': {'human': 'user', 'ai': 'assistant'}.get(m.type, m.type), 'content': m.content} for m in messages]


def review_flags(suite, output, expected, scores):
    flags = {'review_required': 0}
    if suite == 'memory' and scores.get('correctness', 0) and expected['expected_facts']:
        reply = output['turns'][0]['reply'].casefold()
        if not any(fact[language].casefold() in reply for fact in expected['expected_facts'] for language in ('ko', 'en')):
            # shortcut: absent reference wording only requests review; paraphrases need semantic review.
            flags['review_required'] = 1
    if suite == 'dialogue':
        # shortcut: scripted closings prohibit questions; review quoted or rhetorical question marks manually.
        flags['closing_question_mark'] = int('?' in output['turns'][-1]['reply'])
        if flags['closing_question_mark'] and scores.get('dialogue_flow') == 2:
            flags['review_required'] = 1
    return flags


class Benchmark:
    def __init__(self, judge_model, report_dir):
        self.client = get_client()
        if self.client is None or not self.client.auth_check():
            raise RuntimeError('Enable Langfuse and configure working project credentials')
        self.config = get_settings()
        self.judge_model = judge_model
        self.rubrics = read('rubrics.json')
        self.manifest = read('manifest.json')
        self.sources = {read(path)['set_id']: read(path) for path in self.manifest['suites']['memory']['source_files']}
        names = ['talky/system', 'talky/ko-coach', 'talky/en-reply', 'talky/better-expression']
        names += [suite['judge_name'] for suite in self.manifest['suites'].values()]
        self.refs = {name: resolve_reference(name) for name in names}
        self.prompts = {name: remote_prompt(name, version=ref['version']) for name, ref in self.refs.items()}
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
        self.report_dir = report_dir / self.run_id
        self.report_dir.mkdir(parents=True)
        index_spec = {'sources': self.sources, 'topic_prompt': TOPIC_PROMPT, 'model': self.config.MODEL_NAME, 'max_tokens': self.config.LLM_MAX_OUTPUT_TOKENS}
        self.namespace = 'eval-' + hashlib.sha256(json.dumps(index_spec, sort_keys=True).encode()).hexdigest()[:16]
        self.graph_ready = False
        self.judges = {}
        metadata = {'run_id': self.run_id, 'dataset_version': self.manifest['version'], 'model': self.config.OPENAI_MODEL if self.config.use_openai else self.config.MODEL_NAME, 'judge_model': judge_model, 'prompt_refs': self.refs, 'scope': 'text_components; memory_and_dialogue_use_actual_neo4j_retrieval', 'reference_status': self.manifest['reference_status']}
        metadata['code_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        metadata['code_dirty'] = subprocess.run(['git', 'diff', '--quiet'], cwd=ROOT).returncode != 0
        metadata['runner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        metadata['judge_options'] = {'temperature':0, 'num_ctx':16384, 'num_predict':768, 'timeout_seconds':180}
        (self.report_dir / 'run.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        self.metadata = metadata

    def append(self, suite, row):
        with (self.report_dir / f'{suite}.jsonl').open('a') as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')

    def prepare_graph(self):
        if self.graph_ready:
            return
        driver = get_driver()
        driver.verify_connectivity()
        cache_key = self.namespace
        with driver.session() as session:
            ready = session.run('MATCH (f:EvaluationFixture {id:$id}) RETURN f.complete AS complete, f.namespace AS namespace', id=cache_key).single()
        if ready and ready['complete']:
            self.namespace = ready['namespace']
        if not ready or not ready['complete']:
            # Failed imports remain inspectable; retry in a fresh scope, without replaying NEXT edges.
            self.namespace = cache_key + '-' + uuid.uuid4().hex[:6]
            for set_id, source in self.sources.items():
                for original_session in source['sessions']:
                    print(f'Indexing {set_id}/{original_session["id"]}', flush=True)
                    messages = original_session['messages']
                    for user, assistant in zip(messages[::2], messages[1::2]):
                        topics = _extract_topics(user['content'], raise_errors=True)
                        ok = ingest_turn(conversation_id=f'{self.namespace}:{original_session["id"]}', user_id=f'{self.namespace}:{set_id}', user_msg_id=f'{self.namespace}:{user["id"]}', user_text=user['content'], language=user['language'], assistant_msg_id=f'{self.namespace}:{assistant["id"]}', ai_text=assistant['content'])
                        if not ok:
                            raise RuntimeError('Fixture message ingestion failed')
                        if topics and not ingest_topics(conversation_id=f'{self.namespace}:{original_session["id"]}', topics=[f'{self.namespace}/{topic}' for topic in topics]):
                            raise RuntimeError('Fixture topic ingestion failed')
            with driver.session() as session:
                session.run('MERGE (f:EvaluationFixture {id:$id}) SET f.complete=true, f.namespace=$namespace', id=cache_key, namespace=self.namespace).consume()
        self.graph_ready = True
        self.metadata['graph_namespace'] = self.namespace
        self.metadata['neo4j_uri'] = self.config.NEO4J_URI
        (self.report_dir / 'run.json').write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2) + '\n')
        print(f'Neo4j fixture ready: {self.namespace}', flush=True)

    def retrieve(self, set_id, question, case_id):
        topics = _extract_topics(question, raise_errors=True)
        memory = recall_related(f'{self.namespace}:{set_id}', [f'{self.namespace}/{topic}' for topic in topics], current_conversation_id=f'{self.namespace}:trial:{case_id}', raise_errors=True)
        for item in memory:
            item['source_message_id'] = item['message_id'].split(':', 1)[1]
            item['topic'] = item['topic'].split('/', 1)[1]
            if item.get('ts') is not None:
                item['ts'] = str(item['ts'])
        return memory, topics

    def generate(self, task, user_text, history=None, memory=None):
        state = {'user_text': user_text, 'history': bounded_history(history or []), 'retrieved_memory': memory or [], 'system_prompt': self.prompts['talky/system'].prompt, 'common_prompt_texts': {name: p.prompt for name, p in self.prompts.items()}, 'common_prompt_refs': self.refs, 'system_prompt_ref': self.refs['talky/system'], 'trace_id': self.client.get_current_trace_id()}
        if task == 'en_correction':
            name, schema, builder = 'talky/better-expression', better_expr.BetterExpressionOutput, better_expr._build_messages
        elif task == 'ko':
            name, schema, builder = 'talky/ko-coach', ko_coach.KoCoachOutput, ko_coach._build_messages
        else:
            name, schema, builder = 'talky/en-reply', None, en_reply._build_messages
        messages = bounded_messages(builder(state))
        llm = get_structured_llm(schema) if schema else get_chat_llm(streaming=False)
        value = invoke_with_prompt(llm, messages, state, name)
        candidate = value.model_dump() if schema else {'reply': value.content}
        if not all(isinstance(value, str) and value.strip() for value in candidate.values()):
            raise ValueError('The generation contains an empty or non-text output field')
        if schema is ko_coach.KoCoachOutput:
            candidate['english_expression'] = value.english_expression
        visible = serialize_messages(messages)
        return candidate, visible

    def task(self, *, item, **kwargs):
        inputs, metadata = item['input'], item['metadata']
        suite, cid = metadata['suite'], metadata['case_id']
        if metadata.get('calibration_output') is not None:
            print(f'[{suite}] {cid}: using calibration candidate', flush=True)
            return metadata['calibration_output']
        print(f'[{suite}] {cid}: generating', flush=True)
        try:
            with propagate_attributes(session_id=f'{self.run_id}:{cid}'):
                if suite == 'expression':
                    history = [{'role': 'user', 'content': inputs['context']}]
                    candidate, visible = self.generate('ko' if inputs['task'] == 'ko_to_en' else 'en_correction', inputs['user_text'], history)
                    output = {'candidate': candidate, 'model_visible_input': visible, 'context_provided':any(inputs['context'] in message['content'] for message in visible)}
                else:
                    history, turns = [], []
                    user_turns = inputs['turns'] if suite == 'dialogue' else [{'id': cid, 'user_text': inputs['question'], 'language': inputs['language']}]
                    for turn in user_turns:
                        memory, topics = self.retrieve(inputs['set_id'], turn['user_text'], cid)
                        candidate, visible = self.generate(turn['language'], turn['user_text'], history, memory)
                        reply = candidate['reply']
                        turns.append({'turn_id': turn['id'], 'user_text': turn['user_text'], 'reply': reply, 'retrieved_messages': memory, 'topics': topics, 'model_visible_input': visible})
                        history.extend([{'role': 'user', 'content': turn['user_text']}, {'role': 'assistant', 'content': reply}])
                        print(f'[{suite}] {cid}: generated {len(turns)}/{len(user_turns)}', flush=True)
                    output = {'turns': turns}
            self.append(suite, {'case_id': cid, 'stage': 'generation', 'output': output})
            self.client.flush()
            return output
        except Exception as exc:
            self.append(suite, {'case_id': cid, 'stage': 'generation_error', 'error_type': type(exc).__name__})
            raise

    def evaluate(self, *, input, output, expected_output, metadata, **kwargs):
        metadata = {**metadata, 'context_provided':output.get('context_provided')}
        suite, cid = metadata['suite'], metadata['case_id']
        name = self.manifest['suites'][suite]['judge_name']
        schema = judge_schema(suite, input, expected_output, metadata)
        judge_key = json.dumps(schema.model_json_schema(), sort_keys=True)
        if judge_key not in self.judges:
            self.judges[judge_key] = ChatOllama(model=self.judge_model, base_url=self.config.OLLAMA_BASE_URL, temperature=0, num_ctx=16384, num_predict=768, client_kwargs={'timeout':180}).with_structured_output(schema, method='json_schema')
        payload = {'expected_output_for_judge_only': expected_output, 'rubric': self.rubrics['suites'][suite], 'policy': self.rubrics['policy']}
        if suite == 'memory':
            turn = output['turns'][0]
            payload.update(question=input['question'], candidate_answer=turn['reply'], model_visible_context=turn['model_visible_input'], expected_behavior=expected_output.get('expected_behavior', 'answer'))
            payload['source_conversation_for_judge_only'] = self.sources[input['set_id']]
        elif suite == 'dialogue':
            payload['conversation_to_evaluate'] = [{'turn_id':t['turn_id'],'user':t['user_text'],'tutor':t['reply']} for t in output['turns']]
            payload['model_visible_context_by_turn'] = {t['turn_id']:t['model_visible_input'] for t in output['turns']}
            payload['applicable_metrics'] = expected_output['applicable_metrics']
            if output.get('evaluation_scope'):
                payload['evaluation_scope'] = output['evaluation_scope']
        else:
            payload.update(task=input['task'], original_utterance=input['user_text'], intended_context=input['context'], register=input['register'], context_provided=output['context_provided'], model_visible_context=output['model_visible_input'], candidate_output=output['candidate'])
        instructions = self.prompts[name].prompt
        instructions += '\nEvaluate only candidate_answer, candidate_output or conversation_to_evaluate. Reference history and reference answers are evidence, not candidates. Score dimensions independently: a grammatical sentence with the wrong meaning fails meaning preservation, but does not become ungrammatical just because its meaning is wrong. Status describes structural availability, not whether an answer is correct.'
        instructions += '\nKeep reason under 100 words and issues to at most 3 short strings.'
        if output.get('evaluation_scope'):
            instructions += '\nThis is a TURN evaluation of only the final supplied tutor reply using its user prefix. Do not require earlier assistant replies, later turns or a completed six-turn scenario. Judge only the named applicable metric.'
        if suite != 'memory':
            instructions += '\nReturn issues as a list of short strings, including offending excerpts and turn IDs, instead of nested issue objects. Return the complete scores dictionary with null for inapplicable metrics.'
        try:
            print(f'[{suite}] {cid}: judging', flush=True)
            judge_messages = [SystemMessage(content=instructions), HumanMessage(content=json.dumps(payload, ensure_ascii=False))]
            with self.client.start_as_current_observation(name=name, as_type='generation', model=self.judge_model, prompt=self.prompts[name], input=serialize_messages(judge_messages), metadata={'output_schema':schema.model_json_schema(), **self.metadata['judge_options']}) as observation:
                verdict = self.judges[judge_key].invoke(judge_messages).model_dump()
                observation.update(output=verdict)
                scores = score_values(suite, verdict, metadata, input, expected_output)
                if suite != 'memory' and verdict['status'] not in ('ok','insufficient_context','invalid_output'):
                    raise ValueError('Invalid judge status')
                observation.update(output={'verdict': verdict, 'effective_scores': scores})
            status = verdict.get('status', 'ok')
            if suite == 'expression' and metadata.get('context_required') and not output['context_provided']:
                status = 'insufficient_context'
                scores['context_delivery'] = 0
            elif suite == 'expression' and metadata.get('context_required'):
                scores['context_delivery'] = 1
            if not metadata.get('calibration_output'):
                scores['generation_success'] = 1
            scores['judge_success'] = 1
            if metadata.get('calibration_expected'):
                labels = metadata['calibration_expected']
                scores['calibration_agreement'] = sum(scores.get(k) == v for k,v in labels.items()) / len(labels)
            if suite == 'memory' and expected_output['evidence'] and not metadata.get('calibration_output'):
                gold = {e['message_id'] for e in expected_output['evidence']}
                turn = output['turns'][0]
                retrieved = {m['source_message_id'] for m in turn['retrieved_messages']}
                visible_text = '\n'.join(message['content'] for message in turn['model_visible_input'])
                visible = {m['source_message_id'] for m in turn['retrieved_messages'] if m['text'] in visible_text}
                scores['retrieval_recall'] = len(gold & retrieved) / len(gold)
                scores['visible_evidence_recall'] = len(gold & visible) / len(gold)
            if not metadata.get('calibration_output'):
                scores.update(review_flags(suite, output, expected_output, scores))
            self.append(suite, {'case_id':cid, 'stage':'evaluation', 'status':status, 'scores':scores, 'verdict':verdict})
            self.client.flush()
            print(f'[{suite}] {cid}: {status} {scores}', flush=True)
            return [Evaluation(name=k,value=v,comment='Automatic judge; human review pending.\n'+verdict['reason']) for k,v in scores.items() if v is not None]
        except Exception as exc:
            self.append(suite, {'case_id':cid,'stage':'judge_error','error_type':type(exc).__name__, 'detail':str(exc) if isinstance(exc, ValueError) else type(exc).__name__, 'verdict':locals().get('verdict')})
            print(f'[{suite}] {cid}: judge_error ({type(exc).__name__})', flush=True)
            return [Evaluation(name='generation_success',value=1), Evaluation(name='judge_success',value=0,comment=type(exc).__name__)]

    def run(self, suite, items):
        if suite not in ('expression', 'calibration'):
            self.prepare_graph()
        result = self.client.run_experiment(name=f'talky-{suite}-v2', run_name=f'{self.run_id}-{suite}', data=items, task=self.task, evaluators=[self.evaluate], max_concurrency=1, metadata={'run_id':self.run_id,'dataset_version':'2.0','model':self.metadata['model'],'judge_model':self.judge_model,'code_commit':self.metadata['code_commit']})
        self.client.flush()
        summary = {'suite':suite,'planned':len(items),'experiment_url':result.experiment_url,'experiment_id':result.experiment_id,'items':[]}
        for item in result.item_results:
            summary['items'].append({'case_id':item.item['metadata']['case_id'],'trace_id':item.trace_id,'trace_url':self.client.get_trace_url(trace_id=item.trace_id),'scores':{e.name:e.value for e in item.evaluations}})
        values = defaultdict(list)
        for item in summary['items']:
            for name, value in item['scores'].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[name].append(value)
        summary['metric_summary'] = {name:{'n':len(numbers),'mean':sum(numbers)/len(numbers)} for name,numbers in values.items()}
        summary['judged'] = sum(item['scores'].get('judge_success') == 1 for item in summary['items'])
        summary['judge_errors'] = sum(item['scores'].get('judge_success') == 0 for item in summary['items'])
        (self.report_dir/f'{suite}-summary.json').write_text(json.dumps(summary, ensure_ascii=False,indent=2)+'\n')
        print(f'Experiment: {result.experiment_url}\nLocal report: {self.report_dir}',flush=True)
        return summary

    def calibration_items(self):
        cases = {item['metadata']['case_id']:item for suite in self.manifest['suites'] for item in items_for(suite)}
        result = []
        for example in read('calibration/examples.json')['cases']:
            item = copy.deepcopy(cases[example['case_id']])
            metadata = item['metadata']
            metadata.update(case_id=example['id'], category=example['suite'], calibration_expected=example['expected_scores'])
            if example['suite'] == 'expression':
                output = {'candidate':example['candidate_output'],'context_provided':item['input']['task']=='ko_to_en','model_visible_input':[{'role':'user','content':item['input']['user_text']}]}
                if output['context_provided']:
                    output['model_visible_input'].insert(0,{'role':'user','content':item['input']['context']})
            else:
                source = self.sources[item['input']['set_id']]
                messages = [m for session in source['sessions'] for m in session['messages']]
                if example['suite'] == 'memory':
                    visible = [m for m in messages if m['id'] in example['model_visible_message_ids']]
                    output = {'turns':[{'user_text':item['input']['question'],'reply':example['candidate_output'],'retrieved_messages':[], 'model_visible_input':visible}]}
                else:
                    prefix = item['input']['turns'][:example['after_turn']]
                    item['input']['turns'] = prefix
                    item['expected_output']['turns'] = item['expected_output']['turns'][:example['after_turn']]
                    item['expected_output']['applicable_metrics'] = list(example['expected_scores'])
                    output = {'turns':[{'turn_id':turn['id'],'user_text':turn['user_text'],'reply':example['candidate_output'] if i==len(prefix)-1 else None,'model_visible_input':[{'role':'user','content':t['user_text']} for t in prefix[:i+1]]} for i,turn in enumerate(prefix)], 'evaluation_scope':'Only the final provided reply and the named metric are judged; earlier assistant replies were not supplied.'}
            metadata['calibration_output'] = output
            result.append(item)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=['calibration','expression','memory','dialogue','all'],required=True)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--cases',help='Comma-separated case IDs within one suite')
    parser.add_argument('--judge-model',required=True)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    if args.cases and args.suite == 'all':
        parser.error('--cases needs a single suite')
    benchmark = Benchmark(args.judge_model, ROOT/'.data/evaluations')
    suites = ['expression','memory','dialogue'] if args.suite == 'all' else [args.suite]
    try:
        for suite in suites:
            data = benchmark.calibration_items() if suite == 'calibration' else items_for(suite)
            benchmark.run(suite,select_items(data,args.limit,args.cases))
    finally:
        benchmark.client.flush()


if __name__ == '__main__':
    main()
