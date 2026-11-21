"""Protect literal names before rewriting and restore them without interpretation."""
from dataclasses import dataclass
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import invoke_with_prompt
from app.runtime import bounded_messages


class SpeechRepair(BaseModel):
    source: str = Field(min_length=1, description='Exact ordinary-English source span, with enough context to occur only once. Never include or correct a proper name or technical term.')
    replacement: str = Field(min_length=1, description='A context-supported ordinary-English recognition repair, not a guessed technical name.')


class SourceProtection(BaseModel):
    names: list[str] = Field(max_length=24, description='Exact source substrings for names, brands, acronyms and technical names or unclear technical-name lists. Copy spelling exactly, even if wrong or unfamiliar. Do not infer a canonical name. Exclude ordinary vocabulary.')
    speech_repairs: list[SpeechRepair] = Field(max_length=4, description='Only strongly contextual ordinary-English speech recognition repairs. Empty for typed input. Names and unclear technical terms must never be repaired.')


SOURCE_PROMPT = """Prepare a learner's utterance for rewriting, without rewriting it. Copy names and technical-name spans exactly as heard, including unfamiliar or incorrectly recognized names. Preserve an unclear technical list as one span if necessary. These spans will be protected during editing and restored literally, so never identify a better-known technology. Include names/acronyms and short uncertain technical-name phrases, not complete sentences or surrounding verbs. For example, preserve 'language, language series or something' as written when it is a technical list; do not decode it.

For speech input only, separately propose high-confidence ordinary-English sound-alike repairs supported by immediate context. Both source and replacement must be in English, without Korean words or characters. In an educational English-conversation service with no jogging context, running can plausibly mean learning. In a service for runners or group runs, running is valid and must stay. Use an exact, uniquely occurring source span. Never apply a repair to a protected name or uncertain technical term. For typed text, speech_repairs must be empty. Return only literal names and these separate ordinary-word repairs; do not add facts or guess expertise."""


@dataclass
class ProtectedRewrite:
    state: dict
    names: dict[str, str]
    repairs: list[SpeechRepair]


def prepare_rewrite(state):
    original = state['user_text']
    plan = invoke_with_prompt(get_structured_llm(SourceProtection, temperature=0),
        bounded_messages([SystemMessage(content=SOURCE_PROMPT), HumanMessage(content=json.dumps({
            'source':original, 'speech':bool(state.get('audio_bytes'))}, ensure_ascii=False))]),
        state, 'talky/rewrite-source-protection')
    if any(not name.strip() or name not in original for name in plan.names):
        raise ValueError('protected name is not an original substring')
    terms = sorted(set(plan.names), key=lambda name: (-len(name), name))
    source = original
    repairs = []
    for repair in plan.speech_repairs if state.get('audio_bytes') else []:
        spans = (repair.source, repair.replacement)
        if (any(not re.search(r'[A-Za-z]', span) or re.search(r'[가-힣ㄱ-ㆎᄀ-ᇿ]', span) for span in spans)
                or source.count(repair.source) != 1 or any(
                name in repair.source or repair.source in name for name in terms)):
            continue
        source = source.replace(repair.source, repair.replacement, 1)
        repairs.append(repair)
    prefix = '__TALKY_NAME_'
    while prefix in original:
        prefix = '_' + prefix
    names = {}
    for term in terms:
        if term not in source:
            continue  # A larger protected span already contains this term.
        marker = f'{prefix}{len(names)}__'
        source = source.replace(term, marker)
        names[marker] = term
    prepared = {**state, 'user_text':source, '_rewrite_names':names}
    return ProtectedRewrite(prepared, names, repairs)


def restore_rewrite(candidate, prepared):
    english = candidate.suggestion if hasattr(candidate, 'suggestion') else candidate.english_expression
    normalized = english
    # Some providers copy the literal name key instead of its marker.
    for marker, original in sorted(prepared.names.items(), key=lambda item: -len(item[1])):
        normalized = normalized.replace(original, marker)
    for marker in prepared.names:
        if normalized.count(marker) != prepared.state['user_text'].count(marker):
            raise ValueError('rewriter changed or dropped a protected name')
    def restore(value):
        if isinstance(value, str):
            for marker, original in prepared.names.items():
                value = value.replace(marker, original)
            return value
        if isinstance(value, list):
            return [restore(item) for item in value]
        if isinstance(value, dict):
            return {key:restore(item) for key,item in value.items()}
        return value
    restored = type(candidate).model_validate(restore(candidate.model_dump()))
    if re.search(r'__TALKY_NAME_\d+__', str(restored.model_dump())):
        raise ValueError('unrestored name marker')
    return restored


def repair_notes(prepared):
    return '\n'.join(f'- 음성 인식 보정 추정: “{repair.source}”를 문맥상 “{repair.replacement}”로 읽었어요. 의도가 다르면 알려주세요.'
                     for repair in prepared.repairs)
