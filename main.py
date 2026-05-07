import hashlib

import streamlit as st
from dotenv import load_dotenv

from app.agents.conversation_graph import build_conversation_graph
from app.agents.ending_graph import build_ending_graph
from app.db.repo import (
    create_conversation,
    delete_conversation,
    ensure_default_user,
    get_active_prompt,
    list_conversations,
    load_messages,
    update_conversation_title,
)
from app.settings import get_settings
from app.ui.audio_widget import mic_input
from app.ui.report_view import render_report

load_dotenv()
cfg = get_settings()


@st.cache_resource
def _bootstrap() -> dict:
    user_id = ensure_default_user()
    prompt = get_active_prompt()
    if prompt is None:
        st.error("Active system prompt not found. Run: python -m app.db.seed")
        st.stop()
    return {
        "user_id": user_id,
        "system_prompt": prompt["content"],
        "prompt_version_id": prompt["id"],
        "graph": build_conversation_graph(),
        "ending_graph": build_ending_graph(),
    }


boot = _bootstrap()
SYSTEM_PROMPT = boot["system_prompt"]
PROMPT_VERSION_ID = boot["prompt_version_id"]
USER_ID = boot["user_id"]
GRAPH = boot["graph"]
ENDING_GRAPH = boot["ending_graph"]

MODEL_LABEL = cfg.OPENAI_MODEL if cfg.use_openai else cfg.MODEL_NAME
SERVER_LABEL = "OpenAI" if cfg.use_openai else cfg.OLLAMA_BASE_URL

st.set_page_config(page_title="Talky")
st.title("Talky - English Conversation AI Agent")

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "editing_conv_id" not in st.session_state:
    st.session_state.editing_conv_id = None
if "pending_audio" not in st.session_state:
    st.session_state.pending_audio = None
if "last_voice_hash" not in st.session_state:
    st.session_state.last_voice_hash = None
if "show_report_for" not in st.session_state:
    st.session_state.show_report_for = None

with st.sidebar:
    st.markdown("### Settings")
    st.text(f"Model: {MODEL_LABEL}")
    st.text(f"Server: {SERVER_LABEL}")

    st.divider()
    st.markdown("### Conversations")

    if st.button("+ New Conversation"):
        cid = create_conversation(user_id=USER_ID)
        st.session_state.conversation_id = cid
        st.session_state.messages = []
        st.session_state.pending_audio = None
        st.session_state.show_report_for = None
        st.rerun()

    if (
        st.session_state.conversation_id is not None
        and st.session_state.show_report_for is None
        and st.session_state.messages
    ):
        if st.button(
            "End Conversation",
            type="primary",
            use_container_width=True,
            key="end_conv",
        ):
            with st.spinner("보고서 생성 중..."):
                ENDING_GRAPH.invoke(
                    {
                        "conversation_id": st.session_state.conversation_id,
                        "user_id": str(USER_ID),
                    }
                )
            st.session_state.show_report_for = st.session_state.conversation_id
            st.rerun()

    conversations = list_conversations(user_id=USER_ID)
    for conv in conversations:
        cid = conv["id"]
        is_active = st.session_state.conversation_id == cid
        is_editing = st.session_state.editing_conv_id == cid

        if is_editing:
            col_input, col_save = st.columns([5, 1])
            with col_input:
                new_title = st.text_input(
                    "title",
                    value=conv["title"] or "",
                    key=f"edit_{cid}",
                    label_visibility="collapsed",
                )
            with col_save:
                if st.button("✓", key=f"ok_{cid}"):
                    if new_title.strip():
                        update_conversation_title(cid, new_title.strip())
                    st.session_state.editing_conv_id = None
                    st.rerun()
        else:
            col1, col2 = st.columns([5, 1])
            with col1:
                label = conv["title"] or "Untitled"
                if st.button(
                    f"{'> ' if is_active else ''}{label}",
                    key=f"conv_{cid}",
                    use_container_width=True,
                ):
                    st.session_state.conversation_id = cid
                    st.session_state.messages = load_messages(cid)
                    st.session_state.pending_audio = None
                    st.session_state.show_report_for = (
                        cid if conv.get("ended_at") else None
                    )
                    st.rerun()
            with col2:
                with st.popover(""):
                    if st.button("Rename", key=f"edit_btn_{cid}", use_container_width=True):
                        st.session_state.editing_conv_id = cid
                        st.rerun()
                    if st.button("Delete", key=f"del_{cid}", use_container_width=True):
                        delete_conversation(cid)
                        if st.session_state.conversation_id == cid:
                            st.session_state.conversation_id = None
                            st.session_state.messages = []
                            st.session_state.pending_audio = None
                            st.session_state.show_report_for = None
                        st.rerun()

    st.markdown(
        """
    <style>
    [data-testid="stSidebar"] button p {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    [data-testid="stSidebar"] [data-testid="stPopover"] > button > svg {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
    }
    [data-testid="stSidebar"] [data-testid="stPopover"] > button {
        gap: 0 !important;
    }
    </style>
    """,
        unsafe_allow_html=True,
    )


def _ensure_conversation(seed_text: str) -> str:
    if st.session_state.conversation_id is None:
        title = (seed_text[:30] + ("..." if len(seed_text) > 30 else "")) if seed_text else "New Conversation"
        cid = create_conversation(title=title, user_id=USER_ID)
        st.session_state.conversation_id = cid
    return st.session_state.conversation_id


def _render_message(m: dict) -> None:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "user" and m.get("english_expression"):
            st.markdown(
                f"> 💡 **영어로는:** {m['english_expression']}"
            )
        if m["role"] == "assistant" and m.get("better_expression"):
            with st.expander("✨ 참고하세요!"):
                st.markdown(m["better_expression"])


def _push_session_messages(
    cid: str,
    user_text: str,
    ai_reply: str,
    language: str | None,
    english_expression: str | None,
    better_expression: str | None,
) -> None:
    """graph 의 persist 노드가 DB 저장을 처리하므로, 여기선 화면용 세션 상태만 갱신."""
    st.session_state.messages.append(
        {
            "role": "user",
            "content": user_text,
            "language": language,
            "english_expression": english_expression,
            "better_expression": None,
        }
    )
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": ai_reply,
            "language": language,
            "english_expression": None,
            "better_expression": better_expression,
        }
    )

    if len(st.session_state.messages) == 2:
        title = user_text[:30] + ("..." if len(user_text) > 30 else "")
        update_conversation_title(cid, title)


def _invoke_turn(state_input: dict) -> dict:
    return GRAPH.invoke(state_input)


def handle_user_message(prompt: str) -> None:
    st.session_state.pending_audio = None
    cid = _ensure_conversation(prompt)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]
    state_input = {
        "conversation_id": cid,
        "user_id": str(USER_ID),
        "prompt_version_id": PROMPT_VERSION_ID,
        "system_prompt": SYSTEM_PROMPT,
        "user_text": prompt,
        "history": history,
    }
    with st.spinner("생각 중..."):
        result = _invoke_turn(state_input)

    _push_session_messages(
        cid,
        user_text=result.get("user_text", prompt),
        ai_reply=result.get("ai_reply") or "",
        language=result.get("language"),
        english_expression=result.get("english_expression"),
        better_expression=result.get("better_expression"),
    )


def handle_voice_message(audio_bytes: bytes) -> None:
    cid = _ensure_conversation("")
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]
    state_input = {
        "conversation_id": cid,
        "user_id": str(USER_ID),
        "prompt_version_id": PROMPT_VERSION_ID,
        "system_prompt": SYSTEM_PROMPT,
        "audio_bytes": audio_bytes,
        "history": history,
    }
    with st.spinner("듣는 중..."):
        result = _invoke_turn(state_input)

    user_text = (result.get("user_text") or "").strip()
    if not user_text:
        st.warning("음성을 인식하지 못했어요. 다시 말씀해 주세요.")
        return

    _push_session_messages(
        cid,
        user_text=user_text,
        ai_reply=result.get("ai_reply") or "",
        language=result.get("language"),
        english_expression=result.get("english_expression"),
        better_expression=result.get("better_expression"),
    )

    st.session_state.pending_audio = result.get("audio_reply")


if st.session_state.show_report_for:
    render_report(st.session_state.show_report_for, PROMPT_VERSION_ID)
else:
    audio_bytes = mic_input()
    if audio_bytes:
        h = hashlib.sha1(audio_bytes).hexdigest()
        if st.session_state.last_voice_hash != h:
            st.session_state.last_voice_hash = h
            handle_voice_message(audio_bytes)

    for message in st.session_state.messages:
        _render_message(message)

    if st.session_state.pending_audio:
        st.audio(st.session_state.pending_audio, format="audio/wav", autoplay=True)

    text_prompt = st.chat_input("Type a message")
    if text_prompt:
        handle_user_message(text_prompt)
        st.rerun()
