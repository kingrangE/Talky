"""영어 입력에 대한 더 원어민스러운 표현 제안."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.llm.conversation_style import NATIVE_ENGLISH_STYLE, rewrite_fidelity
from app.llm.rewrite_protection import prepare_rewrite, restore_rewrite, repair_notes
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
    suggestion: str = Field(description="One natural spoken-English rewrite preserving every supported fact, subject, ownership, comparison and time relationship. Do not invent technical skills or replace unclear terms with plausible names. An uncertain fragment must not become an asserted fact. Match tone without forcing slang or idioms.")
    note: ExpressionNote = Field(description='Korean translation and contextual grammar/expression explanations in separate structured fields. A rewrite requires explanations; needs_improvement=false may use an empty explanations list.')

    @model_validator(mode='after')
    def require_rewrite_explanations(self):
        if self.needs_improvement and not self.note.explanations:
            raise ValueError('A rewrite requires contextual grammar or expression explanations')
        return self


def _build_messages(state: GraphState) -> list:
    instruction = state.get("common_prompt_texts", {}).get("talky/better-expression", BETTER_EXPR_PROMPT)
    instruction += '\n\nCheck grammar and then conversational phrasing, including common word combinations, verb choices, flow and register. Do not stop because the grammar is correct. For a meaningful native-like improvement, set needs_improvement=true; avoid arbitrary swaps or forced slang. In casual speech, "I have difficulty getting out of bed in the morning." can become "I have a hard time getting out of bed in the morning.", and "I am not accustomed to speaking English." can become "I\'m not used to speaking English." Explain these as optional phrasing improvements, not grammar errors. Keep already idiomatic input unchanged if no meaningful gain exists. Preserve meaning, time, intensity and register.'
    instruction += '\n\nFor this call, note must be an object with translation and explanations, overriding older note-string examples. Put the entire suggestion\'s Korean meaning in translation. When proposing a rewrite, provide at least one learning point; otherwise explanations may be empty. The app makes separate bullets. Inspect actual verb groups, including contractions, and explain meaningful tense/aspect, voice or conditional grammar before easy words. For each used pattern, explain its form, accessible Korean meaning and meaning in THIS sentence; do not just name a tense or repeat the translation. In "I\'ve been getting up around 11 lately", explain have/has been ~ing as a recent repeated routine continuing up to now, not continuous getting out of bed. In "I\'ve visited Fukuoka three times", explain have/has + p.p. as experience up to now. These examples illustrate different constructions, not a reason to change the learner\'s tense. Explain end up ~ing or passive forms when actually used. Match each pattern title to its explanation and the actual words. Never title an absent pattern just to say it is absent. Keep suggestion plain English and explanations concise Korean.'
    if state.get('audio_bytes'):
        instruction += '\nThis is a speech transcript. Capitalization, punctuation or ASR formatting alone do not require a correction.'
    instruction += NATIVE_ENGLISH_STYLE
    instruction += rewrite_fidelity(speech=bool(state.get('audio_bytes')))
    if state.get('_rewrite_names'):
        instruction += '\nLiteral name key (for grammar and pronunciation only): ' + json.dumps(state['_rewrite_names'], ensure_ascii=False)
        instruction += '\nMandatory copy counts in the English rewrite: ' + json.dumps({marker:state['user_text'].count(marker) for marker in state['_rewrite_names']})
        instruction += '\nCopy each occurrence in its original clause. Do not turn repeated named subjects into pronouns: the learner may be comparing their own app with a different named app, and a pronoun changes whose facts are stated. Never translate or identify an unfamiliar literal name. Preserve its marker even in needs_improvement=false output.'

    messages = [
        SystemMessage(content=instruction),
    ]
    for message in bounded_history(state.get('history') or []):
        cls = HumanMessage if message['role'] == 'user' else AIMessage
        messages.append(cls(content=message['content']))
    messages.append(HumanMessage(content=state['user_text']))
    return messages


def needs_correction(state, out):
    if not out.needs_improvement or out.suggestion.strip().rstrip('.!?') == state['user_text'].strip().rstrip('.!?'):
        return False
    return not (state.get('audio_bytes') and
                re.findall(r'\w+', out.suggestion.casefold()) == re.findall(r'\w+', state['user_text'].casefold()))


def better_expr_node(state: GraphState) -> dict:
    try:
        prepared = prepare_rewrite(state)
        llm = get_structured_llm(BetterExpressionOutput)
        out: BetterExpressionOutput = invoke_with_prompt(llm, bounded_messages(_build_messages(prepared.state)), state, "talky/better-expression")
        out = restore_rewrite(out, prepared)
        if not needs_correction(state, out):
            return {'better_expression': None, 'correction_status': 'completed'}
        combined = f"**{out.suggestion}**\n\n{out.note.as_bullets()}"
        if prepared.repairs:
            combined += "\n" + repair_notes(prepared)
        return {"better_expression": combined, "correction_status": "completed"}
    except Exception as exc:
        logging.getLogger('talky.metrics').warning('correction_unavailable error_type=%s', type(exc).__name__)
        return {"better_expression": None, "correction_status": "failed"}
