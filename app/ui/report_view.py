"""대화 종료 보고서 + 별점 UI."""

from __future__ import annotations

import streamlit as st

from app.db.repo import get_rating, get_report, save_rating


def render_report(conversation_id: str, prompt_version_id: str | None) -> None:
    report = get_report(conversation_id)
    st.subheader("📊 Conversation Report")

    if report is None:
        st.info("이 대화에 대한 보고서가 아직 없습니다.")
        return

    if report.get("summary"):
        st.markdown("**Summary**")
        st.markdown(report["summary"])

    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Strengths**")
        for s in report.get("strengths") or []:
            st.markdown(f"- ✓ {s}")
    with cols[1]:
        st.markdown("**Weaknesses**")
        for w in report.get("weaknesses") or []:
            st.markdown(f"- ✗ {w}")

    vocab = report.get("vocab_learned") or []
    if vocab:
        st.markdown("**Vocab learned**")
        st.markdown(" ".join(f"`{v}`" for v in vocab))

    st.divider()

    existing = get_rating(conversation_id) or {}
    st.markdown("### How was this session?")
    initial = (existing.get("stars") or 0) - 1 if existing.get("stars") else None
    stars = st.feedback("stars", key=f"stars_{conversation_id}")
    if stars is None and initial is not None:
        stars = initial
    comment = st.text_input(
        "Comment (optional)",
        value=existing.get("comment") or "",
        key=f"comment_{conversation_id}",
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Save & Back", key=f"save_{conversation_id}"):
            if stars is not None:
                save_rating(
                    conversation_id=conversation_id,
                    prompt_version_id=prompt_version_id,
                    stars=int(stars) + 1,  # st.feedback 은 0~4 반환
                    comment=(comment.strip() or None) if comment else None,
                )
            st.session_state.show_report_for = None
            st.rerun()
