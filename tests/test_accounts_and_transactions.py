from datetime import datetime, timedelta, timezone
import sys

import pytest
from sqlalchemy import event, func, select

from app import auth
from app.agents import evolve_graph
from app.db import repo
from app.db.models import Message, PromptVersion, Rating, User
from app.runtime import ModelBusy, bounded_history, bounded_messages, format_memory, inference_slot

PASSWORD = "test-only-long-password"


def test_password_hashes_are_salted_and_verified():
    first = auth.hash_password(PASSWORD)
    assert first != auth.hash_password(PASSWORD)
    assert PASSWORD not in first
    assert auth.verify_password(PASSWORD, first)
    assert not auth.verify_password("wrong", first)
    assert not auth.verify_password(PASSWORD, None)
    assert not auth.verify_password(PASSWORD, "malformed")
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_accounts_persist_lockout_and_keep_identities_separate(pg_db):
    _, scope = pg_db
    alice = auth.register_user("Alice", PASSWORD)
    bob = auth.register_user("bob", PASSWORD)
    assert alice != bob
    assert auth.authenticate(" ALICE ", PASSWORD) == alice
    with pytest.raises(ValueError):
        auth.register_user("alice", PASSWORD)
    for _ in range(5):
        assert auth.authenticate("alice", "wrong-password") is None
    assert auth.authenticate("alice", PASSWORD) is None
    assert auth.authenticate("bob", PASSWORD) == bob
    with scope() as session:
        user = session.get(User, repo._to_uuid(alice))
        user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert auth.authenticate("alice", PASSWORD) == alice


def test_legacy_data_requires_explicit_admin_account_link(pg_db):
    _, scope = pg_db
    with scope() as session:
        legacy = User(display_name="anonymous")
        session.add(legacy)
        session.flush()
        legacy_id = str(legacy.id)
    cid = repo.create_conversation(title="legacy private data", user_id=legacy_id)
    newcomer = auth.register_user("newcomer", PASSWORD)
    assert repo.list_conversations(newcomer) == []
    assert auth.register_user("owner", PASSWORD, existing_user_id=legacy_id) == legacy_id
    assert repo.list_conversations(auth.authenticate("owner", PASSWORD))[0]["id"] == cid
    with pytest.raises(ValueError):
        auth.register_user("another", PASSWORD, existing_user_id=legacy_id)


def test_every_conversation_access_checks_owner(pg_db, monkeypatch):
    alice = auth.register_user("alice", PASSWORD)
    bob = auth.register_user("bob", PASSWORD)
    cid = repo.create_conversation(user_id=alice)
    repo.save_turn(cid, alice, "private user text", "private reply")
    repo.save_report(conversation_id=cid, user_id=alice, summary="private report",
                     strengths=[], weaknesses=[], vocab_learned=[])
    assert repo.list_conversations(bob) == []
    for operation in (
        lambda: repo.load_messages(cid, bob),
        lambda: repo.get_report(cid, bob),
        lambda: repo.get_rating(cid, bob),
        lambda: repo.update_conversation_title(cid, "stolen", bob),
        lambda: repo.delete_conversation(cid, bob),
        lambda: repo.save_turn(cid, bob, "spoofed", "reply"),
        lambda: repo.save_report(conversation_id=cid, user_id=bob, summary="spoofed",
                                 strengths=[], weaknesses=[], vocab_learned=[]),
    ):
        with pytest.raises(ValueError, match="belong"):
            operation()
    assert len(repo.load_messages(cid, alice)) == 2


def test_second_message_failure_rolls_back_entire_turn(pg_db):
    _, scope = pg_db
    uid = auth.register_user("alice", PASSWORD)
    cid = repo.create_conversation(user_id=uid)

    def fail_assistant(_mapper, _connection, message):
        if message.role == "assistant":
            raise RuntimeError("injected assistant insert failure")

    event.listen(Message, "before_insert", fail_assistant)
    try:
        with pytest.raises(RuntimeError):
            repo.save_turn(cid, uid, "must roll back", "reply")
    finally:
        event.remove(Message, "before_insert", fail_assistant)
    with scope() as session:
        assert session.scalar(select(func.count(Message.id))) == 0


def _rated_conversation(uid, stars=5):
    cid = repo.create_conversation(user_id=uid)
    prompt = repo.get_conversation_prompt(cid, uid)
    repo.save_report(conversation_id=cid, user_id=uid, summary="summary",
                     strengths=[], weaknesses=[], vocab_learned=[])
    rid = repo.save_rating(conversation_id=cid, user_id=uid, prompt_version_id=prompt["id"], stars=stars)
    return cid, prompt, rid


def _snapshot(uid):
    state = {"user_id": uid, "threshold": 1}
    state.update(evolve_graph.should_evolve_node(state))
    state.update(evolve_graph.fetch_node(state))
    state["new_prompt_content"] = "English tutor adapted to the user"
    return state


def test_evolution_only_consumes_ratings_it_actually_read(pg_db):
    uid = auth.register_user("alice", PASSWORD)
    _, original, _ = _rated_conversation(uid)
    state = _snapshot(uid)
    _rated_conversation(uid, 1)
    evolve_graph.insert_node(state)
    assert repo.count_unconsumed_ratings(uid, original["id"])[0] == 1


def test_evolution_aborts_if_snapshotted_rating_was_edited(pg_db):
    uid = auth.register_user("alice", PASSWORD)
    cid, original, _ = _rated_conversation(uid)
    state = _snapshot(uid)
    repo.save_rating(conversation_id=cid, user_id=uid, prompt_version_id=original["id"], stars=1)
    with pytest.raises(ValueError, match="changed"):
        evolve_graph.insert_node(state)
    assert repo.get_active_prompt(uid)["id"] == original["id"]
    assert repo.count_unconsumed_ratings(uid, original["id"])[0] == 1


def test_rating_consumption_failure_rolls_back_prompt_activation(pg_db):
    _, scope = pg_db
    uid = auth.register_user("alice", PASSWORD)
    _, original, _ = _rated_conversation(uid)
    state = _snapshot(uid)

    def fail_consumption(_mapper, _connection, rating):
        if rating.consumed_for_evolution:
            raise RuntimeError("injected consumption failure")

    event.listen(Rating, "before_update", fail_consumption)
    try:
        with pytest.raises(RuntimeError):
            evolve_graph.insert_node(state)
    finally:
        event.remove(Rating, "before_update", fail_consumption)
    assert repo.get_active_prompt(uid)["id"] == original["id"]
    with scope() as session:
        assert session.scalar(select(func.count(PromptVersion.id))) == 1


def test_inference_slot_is_shared_and_released_after_errors(pg_db):
    with pytest.raises(RuntimeError, match="injected"):
        with inference_slot():
            with pytest.raises(ModelBusy):
                with inference_slot():
                    pytest.fail("second connection entered the model slot")
            raise RuntimeError("injected")
    with inference_slot():
        pass


def test_history_has_a_utf8_byte_budget(monkeypatch):
    monkeypatch.setattr("app.runtime.get_settings", lambda: type("Config", (), {"MAX_HISTORY_BYTES": 1024})())
    history = [{"role": role, "content": "안녕" * 20} for _ in range(30) for role in ("user", "assistant")]
    bounded = bounded_history(history)
    assert bounded and len(bounded) < len(history)
    assert bounded[0]["role"] == "user"
    assert sum(len(item["content"].encode()) + 16 for item in bounded) <= 1024


def test_full_prompt_budget_keeps_instructions_and_current_input(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    monkeypatch.setattr("app.runtime.get_settings", lambda: type("Config", (), {"LLM_MAX_INPUT_BYTES": 1024})())
    messages = [SystemMessage(content="instructions"), HumanMessage(content="x" * 800),
                AIMessage(content="old answer"), HumanMessage(content="current question")]
    selected = bounded_messages(messages)
    assert selected[0] is messages[0]
    assert selected[-1] is messages[-1]
    assert messages[1] not in selected
    with pytest.raises(ValueError):
        bounded_messages([messages[0], HumanMessage(content="한글" * 1000)])
    memory = format_memory([{"topic": "한글" * 1000, "text": "한글" * 10000}], "memory\n")
    assert len(memory.encode()) <= 4000


def test_login_ui_keeps_browser_accounts_separate(pg_db, monkeypatch):
    from streamlit.testing.v1 import AppTest

    # Streamlit replaces __main__; restore it so later spawn tests start pytest, not the UI.
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])

    alice = auth.register_user("alice", PASSWORD)
    auth.register_user("bob", PASSWORD)
    repo.create_conversation(title="alice-private-conversation", user_id=alice)

    for name, should_see_private in (("alice", True), ("bob", False)):
        app = AppTest.from_file("main.py").run(timeout=15)
        assert not app.exception
        assert app.title[0].value == "Talky 로그인"
        app.text_input[0].set_value(name)
        app.text_input[1].set_value(PASSWORD)
        app.button[0].click().run(timeout=15)
        assert not app.exception
        labels = [button.label for button in app.button]
        assert any("alice-private-conversation" in label for label in labels) is should_see_private


def test_inference_capacity_is_bounded_and_releases_slots(pg_db, monkeypatch):
    from app.settings import get_settings
    monkeypatch.setenv('INFERENCE_CAPACITY', '2')
    get_settings.cache_clear()
    with inference_slot(), inference_slot():
        with pytest.raises(ModelBusy):
            with inference_slot():
                pytest.fail('capacity exceeded')
    with inference_slot(), inference_slot():
        pass
