import hashlib

import streamlit as st
from dotenv import load_dotenv

from app.agents.conversation_graph import build_conversation_graph
from app.db.repo import (
    create_conversation,
    delete_conversation,
    ensure_default_user,
    get_active_prompt,
    list_conversations,
    load_messages,
    save_message,
    update_conversation_title,
)
from app.settings import get_settings
from app.ui.audio_widget import mic_input

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
    }


boot = _bootstrap()
SYSTEM_PROMPT = boot["system_prompt"]
PROMPT_VERSION_ID = boot["prompt_version_id"]
USER_ID = boot["user_id"]
GRAPH = boot["graph"]

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
        title = seed_text[:30] + ("..." if len(seed_text) > 30 else "")
        cid = create_conversation(title=title, user_id=USER_ID)
        st.session_state.conversation_id = cid
    return st.session_state.conversation_id


def _stream_text_turn(state_input: dict):
    for chunk, _meta in GRAPH.stream(state_input, stream_mode="messages"):
        text = getattr(chunk, "content", None)
        if text:
            yield text


def handle_user_message(prompt: str) -> None:
    st.session_state.pending_audio = None
    cid = _ensure_conversation(prompt)

    st.session_state.messages.append({"role": "user", "content": prompt})
    save_message(cid, "user", prompt)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]
    state_input = {
        "conversation_id": cid,
        "user_id": str(USER_ID),
        "prompt_version_id": PROMPT_VERSION_ID,
        "system_prompt": SYSTEM_PROMPT,
        "user_text": prompt,
        "history": history,
    }

    with st.chat_message("assistant"):
        response = st.write_stream(_stream_text_turn(state_input))

    st.session_state.messages.append({"role": "assistant", "content": response})
    save_message(cid, "assistant", response)
    if len(st.session_state.messages) == 2:
        title = prompt[:30] + ("..." if len(prompt) > 30 else "")
        update_conversation_title(cid, title)


def handle_voice_message(audio_bytes: bytes) -> None:
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]
    state_input = {
        "conversation_id": st.session_state.conversation_id or "",
        "user_id": str(USER_ID),
        "prompt_version_id": PROMPT_VERSION_ID,
        "system_prompt": SYSTEM_PROMPT,
        "audio_bytes": audio_bytes,
        "history": history,
    }
    with st.spinner("듣는 중..."):
        result = GRAPH.invoke(state_input)

    user_text = (result.get("user_text") or "").strip()
    ai_reply = result.get("ai_reply") or ""
    audio_reply = result.get("audio_reply")
    language = result.get("language")

    if not user_text:
        st.warning("음성을 인식하지 못했어요. 다시 말씀해 주세요.")
        return

    cid = _ensure_conversation(user_text)
    st.session_state.messages.append({"role": "user", "content": user_text})
    save_message(cid, "user", user_text, language=language)
    st.session_state.messages.append({"role": "assistant", "content": ai_reply})
    save_message(cid, "assistant", ai_reply, language=language)

    if len(st.session_state.messages) == 2:
        title = user_text[:30] + ("..." if len(user_text) > 30 else "")
        update_conversation_title(cid, title)

    st.session_state.pending_audio = audio_reply


# --- Voice 입력 (텍스트 입력보다 먼저 처리해서 history 가 새 메시지를 포함하게) ---
audio_bytes = mic_input()
if audio_bytes:
    h = hashlib.sha1(audio_bytes).hexdigest()
    if st.session_state.last_voice_hash != h:
        st.session_state.last_voice_hash = h
        handle_voice_message(audio_bytes)

# --- 히스토리 표시 ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- 응답 자동 재생 ---
if st.session_state.pending_audio:
    st.audio(st.session_state.pending_audio, format="audio/wav", autoplay=True)

# --- 텍스트 입력 (fallback) ---
text_prompt = st.chat_input("Type a message")
if text_prompt:
    handle_user_message(text_prompt)
    st.rerun()
