import hashlib
import uuid

import streamlit as st

# set_page_config 는 다른 streamlit 호출보다 먼저 실행되어야 함.
# (cache_resource 의 기본 spinner 가 첫 streamlit 명령으로 카운트되므로
#  bootstrap 호출 전에 호출.)
st.set_page_config(page_title="Talky")

from dotenv import load_dotenv  # noqa: E402

from app.conversation import submit_turn, get_turn, active_turn  # noqa: E402
from app.mock_exam.privacy import audio_store  # noqa: E402
from app.agents.ending_graph import build_ending_graph  # noqa: E402
from app.db.repo import (  # noqa: E402
    create_conversation,
    delete_conversation,
    list_conversations,
    load_messages,
    update_conversation_title,
)
from app.settings import get_settings  # noqa: E402
from app.ui.audio_widget import mic_input  # noqa: E402
from app.ui.report_view import render_report  # noqa: E402
from app.ui.login import require_user  # noqa: E402
from app.runtime import ModelBusy, inference_slot  # noqa: E402
from app.llm.prompt_registry import PromptUnavailable  # noqa: E402

load_dotenv()
cfg = get_settings()

with st.sidebar:
    app_mode = st.radio(
        "Mode",
        ("English Conversation", "Speaking Mock Test"),
        key="app_mode",
    )

if app_mode == "Speaking Mock Test":
    from app.ui.mock_exam_view import render_mock_exam  # noqa: E402

    render_mock_exam()
    st.stop()


@st.cache_resource
def _bootstrap() -> dict:
    return {
        "ending_graph": build_ending_graph(),
    }


USER_ID = require_user()
boot = _bootstrap()
ENDING_GRAPH = boot["ending_graph"]


MODEL_LABEL = cfg.OPENAI_MODEL if cfg.use_openai else cfg.MODEL_NAME
SERVER_LABEL = "OpenAI" if cfg.use_openai else cfg.OLLAMA_BASE_URL


st.title("Talky - English Conversation AI Agent")

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "pending_turn_id" not in st.session_state:
    st.session_state.pending_turn_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "editing_conv_id" not in st.session_state:
    st.session_state.editing_conv_id = None
if "pending_audio" not in st.session_state:
    st.session_state.pending_audio = None
if "last_voice_hash" not in st.session_state:
    st.session_state.last_voice_hash = None
if "voice_turn" not in st.session_state:
    st.session_state.voice_turn = 0
if "show_report_for" not in st.session_state:
    st.session_state.show_report_for = None

with st.sidebar:
    st.markdown("### Settings")
    st.text(f"Model: {MODEL_LABEL}")
    st.text(f"Server: {SERVER_LABEL}")

    st.divider()
    st.markdown("### Conversations")

    if st.button("+ New Conversation"):
        try:
            cid = create_conversation(user_id=USER_ID)
            st.session_state.conversation_id = cid
            st.session_state.pending_turn_id = None
            st.session_state.messages = []
            st.session_state.pending_audio = None
            st.session_state.show_report_for = None
            st.rerun()
        except PromptUnavailable as exc:
            st.warning(str(exc))

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
            try:
                with st.spinner("보고서 생성 중..."), inference_slot():
                    ENDING_GRAPH.invoke({
                        "conversation_id": st.session_state.conversation_id,
                        "user_id": str(USER_ID),
                    })
                st.session_state.show_report_for = st.session_state.conversation_id
                st.rerun()
            except (ModelBusy, ValueError, PromptUnavailable) as exc:
                st.warning(str(exc))

    page = st.session_state.get("conversation_page", 0)
    conversations = list_conversations(user_id=USER_ID, offset=page * 50)
    previous, following = st.columns(2)
    if previous.button("이전 목록", disabled=page == 0):
        st.session_state.conversation_page = page - 1
        st.rerun()
    if following.button("다음 목록", disabled=len(conversations) < 50):
        st.session_state.conversation_page = page + 1
        st.rerun()
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
                        update_conversation_title(cid, new_title.strip(), USER_ID)
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
                    pending = active_turn(cid, USER_ID)
                    st.session_state.pending_turn_id = pending["id"] if pending else None
                    st.session_state.messages = load_messages(cid, USER_ID)
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
                        delete_conversation(cid, USER_ID)
                        if st.session_state.conversation_id == cid:
                            st.session_state.conversation_id = None
                            st.session_state.pending_turn_id = None
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


def _submit(**payload) -> None:
    cid = _ensure_conversation(payload.get("user_text") or "")
    from app.ui.submission import submit_pending
    submit_pending(st.session_state, submit_turn, conversation_id=cid,
                   user_id=USER_ID, **payload)
    st.session_state.pending_audio = None


def handle_user_message(prompt: str) -> None:
    _submit(user_text=prompt)


def handle_voice_message(audio_bytes: bytes) -> None:
    _submit(audio_bytes=audio_bytes)


@st.fragment(run_every=1)
def _poll_turn() -> None:
    rid = st.session_state.pending_turn_id
    if not rid:
        return
    try:
        job = get_turn(rid, USER_ID)
    except ValueError:
        st.session_state.pending_turn_id = None
        st.rerun()
    if (job.get("result") and job["conversation_id"] == st.session_state.conversation_id
            and job["status"] in {"queued", "processing"}):
        committed = load_messages(job["conversation_id"], USER_ID)
        if committed != st.session_state.messages:
            st.session_state.messages = committed
            st.rerun()
    if job["status"] in {"queued", "processing"}:
        st.info("응답을 준비하고 있습니다. 다른 대화를 열어도 처리는 계속됩니다.")
        return
    st.session_state.pending_turn_id = None
    st.session_state.messages = load_messages(job["conversation_id"], USER_ID)
    if job["status"] == "completed":
        if job["output_audio_path"]:
            try:
                st.session_state.pending_audio = audio_store().load(job["output_audio_path"])
            except (OSError, ValueError):
                st.warning("음성을 불러오지 못했습니다. 대화 내용은 저장되었습니다.")
    else:
        st.session_state.turn_error = "응답을 완료하지 못했습니다. 잠시 후 다시 보내주세요."
    st.rerun()


if st.session_state.show_report_for:
    render_report(st.session_state.show_report_for, str(USER_ID))
else:
    if st.session_state.pop("turn_error", None):
        st.warning("응답을 완료하지 못했습니다. 잠시 후 다시 보내주세요.")
    _poll_turn()
    uncertain = st.session_state.get("uncertain_submission")
    if uncertain:
        st.warning("요청 접수 여부를 확인하지 못했습니다. 같은 요청으로 다시 확인해주세요.")
        if st.button("접수 다시 확인"):
            try:
                from app.ui.submission import submit_pending
                submit_pending(st.session_state, submit_turn, **uncertain["intent"])
                st.rerun()
            except Exception:
                st.warning("접수를 확인하지 못했습니다. 잠시 후 다시 확인해주세요.")
    audio_bytes = None if st.session_state.pending_turn_id else mic_input(key=f"voice_input_{st.session_state.voice_turn}")
    if audio_bytes:
        h = hashlib.sha1(audio_bytes).hexdigest()
        if st.session_state.last_voice_hash != h:
            try:
                handle_voice_message(audio_bytes)
                st.session_state.last_voice_hash = h
                st.session_state.voice_turn += 1
                st.rerun()
            except (ModelBusy, ValueError, PromptUnavailable) as exc:
                st.warning(str(exc))
            except Exception:
                st.warning("접수 여부를 확인하지 못했습니다. 접수 다시 확인을 이용해주세요.")

    for message in st.session_state.messages:
        _render_message(message)

    if st.session_state.pending_audio:
        st.audio(st.session_state.pending_audio, format="audio/wav", autoplay=True)
        st.session_state.pending_audio = None

    text_prompt = st.chat_input("Type a message", max_chars=cfg.MAX_USER_TEXT_CHARS, disabled=bool(st.session_state.pending_turn_id))
    if text_prompt:
        try:
            handle_user_message(text_prompt)
            st.rerun()
        except (ModelBusy, ValueError, PromptUnavailable) as exc:
            st.warning(str(exc))
