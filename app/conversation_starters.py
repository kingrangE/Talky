"""AI-first openings are assistant messages, never invented user utterances."""
from secrets import choice
from app.db.postgres import session_scope
from app.db.models import Message, ConversationTurn
from app.db.repo import _owned_conversation, _to_uuid

TOPICS = {
    'daily': 'How has your day been so far?',
    'travel': 'Where would you like to go on your next trip?',
    'food': "What's a meal you could eat every day without getting tired of it?",
    'hobbies': 'What do you like to do in your free time?',
    'work': 'What does a typical weekday look like for you?',
    'random': None,
}


TOPIC_TITLES = {
    "daily": "오늘의 일상", "travel": "가고 싶은 여행지", "food": "좋아하는 음식",
    "hobbies": "여가 시간과 취미", "work": "평일 일과",
}


def starter_node(state):
    topic = state['starter_topic']
    with session_scope() as session:
        conv = _owned_conversation(session, state['conversation_id'], state['user_id'], lock=True)
        turn = session.get(ConversationTurn, _to_uuid(state['request_id']), with_for_update=True)
        if (not turn or turn.user_id != _to_uuid(state['user_id']) or turn.conversation_id != conv.id
                or turn.status != 'processing' or turn.attempts != state['turn_attempt'] or conv.ended_at):
            raise ValueError('Stale conversation opening attempt')
        if turn.result:
            return turn.result
        # The first turn has no prior utterance to react to; only later turns use free generation.
        selected = choice(list(TOPIC_TITLES)) if topic == "random" else topic
        reply = TOPICS[selected]
        if conv.title is None:
            conv.title = TOPIC_TITLES[selected]
        message = Message(conversation_id=conv.id, role='assistant', language='en', content=reply)
        session.add(message)
        session.flush()
        result = {'ai_reply': reply, 'language': 'en', 'user_text': '', 'user_message_id': None,
                  'assistant_message_id': str(message.id), 'starter_topic': topic}
        turn.result = result
    return result
