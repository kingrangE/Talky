"""AI-first openings are assistant messages, never invented user utterances."""
from langchain_core.messages import SystemMessage, HumanMessage
from app.db.postgres import session_scope
from app.db.models import Message, ConversationTurn
from app.db.repo import _owned_conversation, _to_uuid
from app.llm.factory import get_chat_llm
from app.llm.prompt_registry import invoke_with_prompt
from app.runtime import bounded_messages
from app.errors import UserFacingError

TOPICS = {'daily': 'everyday life', 'travel': 'travel', 'food': 'food',
          'hobbies': 'hobbies', 'work': 'work or school', 'random': 'one easy everyday topic of your choice'}


def starter_node(state):
    topic = state['starter_topic']
    system = state['system_prompt'] + '\n\nStart the conversation yourself in English about the selected topic. The user selected a topic but has not spoken yet. Use simple everyday words, one or two friendly sentences and one concrete question they can answer. Do not assume prior experience with the topic. Do not pretend they said anything, invent personal facts, give a lecture, or offer support services.'
    reply = invoke_with_prompt(get_chat_llm(), bounded_messages([
        SystemMessage(content=system), HumanMessage(content='Selected topic: ' + TOPICS[topic]),
    ]), state, 'talky/en-reply').content.strip()
    if not reply:
        raise UserFacingError('response_empty')
    with session_scope() as session:
        conv = _owned_conversation(session, state['conversation_id'], state['user_id'], lock=True)
        turn = session.get(ConversationTurn, _to_uuid(state['request_id']), with_for_update=True)
        if (not turn or turn.user_id != _to_uuid(state['user_id']) or turn.conversation_id != conv.id
                or turn.status != 'processing' or turn.attempts != state['turn_attempt'] or conv.ended_at):
            raise ValueError('Stale conversation opening attempt')
        if turn.result:
            return turn.result
        message = Message(conversation_id=conv.id, role='assistant', language='en', content=reply)
        session.add(message)
        session.flush()
        result = {'ai_reply': reply, 'language': 'en', 'user_text': '', 'user_message_id': None,
                  'assistant_message_id': str(message.id), 'starter_topic': topic}
        turn.result = result
    return result
