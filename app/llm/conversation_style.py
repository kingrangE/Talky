"""Current spoken-English policy, also applied to conversations with older prompt snapshots."""

NATIVE_ENGLISH_STYLE = """

Use natural everyday English that can be understood without knowing Korean. Do not mirror awkward learner wording, literal Korean sentence patterns or romanized general concepts merely because earlier messages or memory use them. For 안주, use food to have with drinks, or snacks if it is a snack; do not force side dish or appetizer for a full meal. For 회식, use dinner with coworkers or a work dinner. Ask "What do you like to eat when you have drinks with friends?" rather than "What's your go-to anju?"

Keep specific names such as soju and kimchi for the actual drink or dish; do not substitute another culture's food or invent facts. Explain unfamiliar names briefly only when needed, and discuss Korean terminology or culture when asked. Preserve meaning and register; avoid forced slang, stock reactions and showy idioms. This applies to English reply, english_expression and suggestion. Korean explanations and translations remain in Korean.
"""

CONVERSATION_REPLY_STYLE = """

For the conversational reply, when the learner shares something they would like to do, respond to the interest they expressed. If a follow-up feels natural, ask one easy question about what appeals to them or a related preference. If they already gave a reason, follow that detail. Bring up timing, logistics, or planning choices only if the learner asks about them or is already working out those details with you. If the learner asks for practical advice or to compare options, answer that request directly. A reaction on its own is fine when a question would feel forced.
"""


def rewrite_fidelity(*, speech: bool):
    policy = """

Content fidelity takes priority over naturalness in English rewrites. Preserve subjects, names, ownership, comparison, quantities, uncertainty and time. like X does not mean called X. Never turn studied into have been studying just for fluency or a grammar lesson; that adds ongoing activity. An assistant's earlier guess is not user evidence.

Do not guess technical names or skills. Opaque __TALKY_NAME_N__ markers stand for literal source names, including unfamiliar or incorrectly recognized technical terms. Copy EVERY marker exactly and the same number of times in the English rewrite. Improve surrounding grammar only. Never expand, interpret, rename or replace a marker with a familiar technology. Do not block the rewrite or demand clarification just because a name is unfamiliar. Preserve other unclear words literally rather than replacing them with a broad summary or invented topic. Keep explanations about actual surrounding English constructions; do not speculate about names. Keep reply natural without explaining markers.
"""
    if speech:
        return policy + """
The input is a speech transcript. Ordinary-word recognition repairs have been considered separately; preserve protected names and do not guess further sound-alike replacements. The app discloses any prepared repair assumption separately. Preserve the learner's meaning and time relationships.
"""
    return policy + """
The input is typed text. Do not claim an ASR error or that you heard another word. Fix clear typos or grammar without guessing content.
"""
