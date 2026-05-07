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

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if "recording" not in st.session_state:
    st.session_state.recording = False


def _stream_graph(state_input: dict):
    """LangGraph 의 LLM 노드에서 발생하는 token chunk 를 yield."""
    for chunk, _meta in GRAPH.stream(state_input, stream_mode="messages"):
        text = getattr(chunk, "content", None)
        if text:
            yield text


def handle_user_message(prompt: str) -> None:
    if st.session_state.conversation_id is None:
        title = prompt[:30] + ("..." if len(prompt) > 30 else "")
        cid = create_conversation(title=title, user_id=USER_ID)
        st.session_state.conversation_id = cid

    st.session_state.messages.append({"role": "user", "content": prompt})
    save_message(st.session_state.conversation_id, "user", prompt)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]
    state_input = {
        "conversation_id": st.session_state.conversation_id,
        "user_id": str(USER_ID),
        "prompt_version_id": PROMPT_VERSION_ID,
        "system_prompt": SYSTEM_PROMPT,
        "user_text": prompt,
        "history": history,
    }

    with st.chat_message("assistant"):
        response = st.write_stream(_stream_graph(state_input))

    st.session_state.messages.append({"role": "assistant", "content": response})
    save_message(st.session_state.conversation_id, "assistant", response)
    if len(st.session_state.messages) == 2:
        title = prompt[:30] + ("..." if len(prompt) > 30 else "")
        update_conversation_title(st.session_state.conversation_id, title)


with st.container():
    if st.session_state.recording:
        if st.button(
            "⏹ Stop Recording", key="voice_stop", type="primary", use_container_width=True
        ):
            st.session_state.recording = False
            # Phase C 에서 STT 연결.
            prompt = ""
            if prompt:
                handle_user_message(prompt)
            st.rerun()
    else:
        if st.button("🎤 Tap to Speak", key="voice_start", use_container_width=True):
            st.session_state.recording = True
            st.rerun()

text_prompt = st.chat_input("Type a message (mic comes in Phase C)")
if text_prompt:
    handle_user_message(text_prompt)
    st.rerun()

st.markdown(
    """
<style>
[data-testid="stBottomBlockContainer"] {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 1rem 2rem;
    background: var(--background-color);
}
</style>
""",
    unsafe_allow_html=True,
)
