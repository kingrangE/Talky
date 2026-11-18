"""영어 입력에 대한 더 원어민스러운 표현 제안."""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.llm.conversation_style import NATIVE_ENGLISH_STYLE
from app.runtime import bounded_history, bounded_messages

BETTER_EXPR_PROMPT = builtin_text("talky/better-expression")


class LearningPoint(BaseModel):
    pattern: str = Field(min_length=1, description='The useful expression or complete grammar construction ACTUALLY USED in suggestion. Identify its real verb form before naming the pattern. Never name an absent construction, even to contrast it with the one used.')
    explanation: str = Field(min_length=1, description='Explain in simple Korean what this pattern means, how it works, and what it means in this particular sentence. Grammar names alone are not explanations.')


class ExpressionNote(BaseModel):
    translation: str = Field(min_length=1, description='Natural Korean translation of the entire suggestion. Translation only; grammar and expression explanations belong in explanations.')
    explanations: list[LearningPoint] = Field(max_length=4, description='When needs_improvement is true, provide at least one learning point grounded in the actual wording of suggestion. Otherwise this list may be empty. Prioritize meaningful verb/tense/aspect/voice constructions before easier vocabulary. The pattern title and explanation must describe the SAME construction used in this sentence. Never add a point about an absent construction.')

    def as_bullets(self):
        lines = [self.translation, *(f'{point.pattern}: {point.explanation}' for point in self.explanations)]
        return '\n'.join('- ' + ' '.join(line.split()) for line in lines)


class BetterExpressionOutput(BaseModel):
    needs_improvement: bool = Field(default=True, description='True for grammar errors OR a meaningful gain in native-like spoken phrasing. Independently assess common word combinations, verb choices, conversational patterns, sentence flow and tone even when grammar is correct. False only when already idiomatic and appropriate with no meaningful gain. Do not invent errors or make arbitrary synonym swaps.')
    suggestion: str = Field(description="One meaning-preserving English rewrite using phrasing a native speaker would naturally choose in this conversation, beyond grammar repair. Match the speaker's tone and situation; do not force slang or idioms.")
    note: ExpressionNote = Field(description='Korean translation and contextual grammar/expression explanations in separate structured fields. A rewrite requires explanations; needs_improvement=false may use an empty explanations list.')

    @model_validator(mode='after')
    def require_rewrite_explanations(self):
        if self.needs_improvement and not self.note.explanations:
            raise ValueError('A rewrite requires contextual grammar or expression explanations')
        return self


def _build_messages(state: GraphState) -> list:
    instruction = state.get("common_prompt_texts", {}).get("talky/better-expression", BETTER_EXPR_PROMPT)
    instruction += '\n\nThe learner wants native-like conversational expression, beyond grammar repair. After checking grammar, independently assess common word combinations, natural verb choices, familiar conversational patterns, repetition, sentence flow and tone. Do not stop because the grammar is correct. Proactively offer one meaning-preserving rewrite when it sounds more typical of native conversation; set needs_improvement=true for this gain even without a grammar error. For casual speech, "I have difficulty getting out of bed in the morning." can become "I have a hard time getting out of bed in the morning.", and "I am not accustomed to speaking English." can become "I\'m not used to speaking English." Preserve meaning, tense, intensity and register. Use phrasal verbs or idioms only when they fit naturally. Explain naturalness changes as optional suggestions, not grammar errors. Keep an already idiomatic, appropriate utterance unchanged when an alternative adds no meaningful benefit; do not force slang, rare words or arbitrary synonym swaps.'
    instruction += '\n\nFor this call, note must be an object with translation and explanations, overriding any older prompt example that asks for a note string. Put only the entire suggestion\'s Korean meaning in translation. In explanations, provide at least one useful pattern and its Korean explanation; the app renders these as separate bullets after the translation. Inspect all verb groups, including contractions, and explain meaningful tense/aspect, voice, conditional, or other grammar constructions before easier words. The learner does not already know these patterns. If suggestion uses have/has been ~ing (including I\'ve been, she\'s been, and negatives), explain the FULL present perfect continuous construction: its form, accessible meaning, and why it fits THIS sentence. For "I\'ve been getting up around 11 lately", explain that a recent repeated routine has been continuing up to now; it does not mean the speaker is continuously getting out of bed. Do not collapse this to have + p.p., explain only get up or lately, or omit it as too basic. Likewise, explain have + p.p., passive forms such as be + p.p., or end up ~ing when they carry important meaning in the actual suggestion. Do not list constructions absent from the suggestion. Do not invent a grammar error, force unrelated grammar lessons, or merely name a tense without explaining it. Keep suggestion as plain English without Markdown and explanations concise Korean, not general praise or another translation.'
    if state.get('audio_bytes'):
        instruction += '\nThis is a speech transcript. Capitalization, punctuation or ASR formatting alone do not require a correction.'
    instruction += NATIVE_ENGLISH_STYLE
    instruction += '\n\nLearning-point grounding check: derive each pattern from the actual words in suggestion BEFORE explaining it. Never create a learning point whose title names an absent construction and whose explanation says it is not used. For "I\'ve visited Fukuoka three times", the correct title is "have/has + p.p. (현재완료)", NOT "have/has been ~ing"; explain the experience up to now. For "I\'ve been getting up around 11 lately", the title is "have/has been ~ing (현재완료진행형)" and the meaning is a recent repeated routine continuing up to now. Keep these different constructions separate. Every title must agree with its explanation and this sentence.'
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
    combined = f"**{out.suggestion}**\n\n{out.note.as_bullets()}"
    return {"better_expression": combined, "correction_status": "completed"}
