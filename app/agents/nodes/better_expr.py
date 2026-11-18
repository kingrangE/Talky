"""영어 입력에 대한 더 원어민스러운 표현 제안."""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.llm.conversation_style import NATIVE_ENGLISH_STYLE
from app.runtime import bounded_history, bounded_messages

BETTER_EXPR_PROMPT = builtin_text("talky/better-expression")


class BetterExpressionOutput(BaseModel):
    needs_improvement: bool = Field(default=True, description='True for grammar errors OR a meaningful gain in native-like spoken phrasing. Independently assess common word combinations, verb choices, conversational patterns, sentence flow and tone even when grammar is correct. False only when already idiomatic and appropriate with no meaningful gain. Do not invent errors or make arbitrary synonym swaps.')
    suggestion: str = Field(description="One meaning-preserving English rewrite using phrasing a native speaker would naturally choose in this conversation, beyond grammar repair. Match the speaker's tone and situation; do not force slang or idioms.")
    note: str = Field(description="한국어 불릿 목록. 첫 줄은 개선 문장 전체의 해석, 이후 각 줄은 문장에 실제 쓰인 주요 표현·문법의 형태와 문맥상 뜻. 각 줄은 '- '로 시작하고 줄바꿈으로 구분한다.")


def _build_messages(state: GraphState) -> list:
    instruction = state.get("common_prompt_texts", {}).get("talky/better-expression", BETTER_EXPR_PROMPT)
    instruction += '\n\nThe learner wants native-like conversational expression, beyond grammar repair. After checking grammar, independently assess common word combinations, natural verb choices, familiar conversational patterns, repetition, sentence flow and tone. Do not stop because the grammar is correct. Proactively offer one meaning-preserving rewrite when it sounds more typical of native conversation; set needs_improvement=true for this gain even without a grammar error. For casual speech, "I have difficulty getting out of bed in the morning." can become "I have a hard time getting out of bed in the morning.", and "I am not accustomed to speaking English." can become "I\'m not used to speaking English." Preserve meaning, tense, intensity and register. Use phrasal verbs or idioms only when they fit naturally. Explain naturalness changes as optional suggestions, not grammar errors. Keep an already idiomatic, appropriate utterance unchanged when an alternative adds no meaningful benefit; do not force slang, rare words or arbitrary synonym swaps.'
    instruction += '\n\nFormat note as Korean bullet points, each on its own line starting with "- ". The first bullet must translate the entire suggestion into Korean. Each following bullet must name a useful expression or grammar pattern actually used in the suggestion and explain its meaning and use in this context in concise Korean (for example, end up ~ing, have + p.p., or taken by ~ only when present). Do not list unrelated patterns, explain every basic word, repeat the translation, or write a general paragraph about why the rewrite sounds better. Keep suggestion as plain English without Markdown.'
    if state.get('audio_bytes'):
        instruction += '\nThis is a speech transcript. Capitalization, punctuation or ASR formatting alone do not require a correction.'
    instruction += NATIVE_ENGLISH_STYLE
    messages = [
        SystemMessage(content=instruction),
    ]
    for message in bounded_history(state.get('history') or []):
        cls = HumanMessage if message['role'] == 'user' else AIMessage
        messages.append(cls(content=message['content']))
    messages.append(HumanMessage(content=state['user_text']))
    return messages


def better_expr_node(state: GraphState) -> dict:
    try:
        llm = get_structured_llm(BetterExpressionOutput)
        out: BetterExpressionOutput = invoke_with_prompt(llm, bounded_messages(_build_messages(state)), state, "talky/better-expression")
    except Exception as exc:
        logging.getLogger('talky.metrics').warning('correction_unavailable error_type=%s', type(exc).__name__)
        return {"better_expression": None, "correction_status": "failed"}
    if not out.needs_improvement or out.suggestion.strip().rstrip('.!?') == state['user_text'].strip().rstrip('.!?'):
        return {'better_expression': None, 'correction_status': 'completed'}
    if state.get('audio_bytes') and re.findall(r'\w+', out.suggestion.casefold()) == re.findall(r'\w+', state['user_text'].casefold()):
        return {'better_expression': None, 'correction_status': 'completed'}
    combined = f"**{out.suggestion}**\n\n{out.note}"
    return {"better_expression": combined, "correction_status": "completed"}
