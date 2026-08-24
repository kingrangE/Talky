"""Streamlit screens for the public anonymous speaking mock test."""

from __future__ import annotations

import base64
import html
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from app.mock_exam.content import list_approved_sets, load_exam_set
from app.mock_exam.narration import narration_for
from app.mock_exam.privacy import (
    InvalidRecoveryToken,
    sign_recovery_token,
    verify_recovery_token,
    visitor_fingerprint,
)
from app.mock_exam.repository import (
    create_session,
    get_report,
    get_session,
    list_responses,
    quota_status,
    save_calibration_sample,
)
from app.mock_exam.service import publish_timeout_report, run_expiry_cleanup, submit_response
from app.settings import get_settings
from app.ui.timed_recorder import timed_recorder


DISCLAIMER_KO = (
    "TOEIC®은 ETS의 등록상표입니다. 이 모의고사는 ETS의 승인 또는 보증을 받지 않았으며, "
    "공식 문항·로고·채점 결과를 사용하지 않습니다. 결과는 학습 참고용 베타 추정치입니다."
)
DISCLAIMER_EN = (
    "TOEIC® is a registered trademark of ETS. This product is not endorsed or approved by ETS. "
    "It uses original practice content and provides an unofficial beta estimate for learning only."
)


def _headers() -> dict[str, str]:
    try:
        return {str(key).lower(): str(value) for key, value in st.context.headers.items()}
    except Exception:
        return {}


def _visitor_hash() -> str:
    if "mock_exam_browser_id" not in st.session_state:
        st.session_state.mock_exam_browser_id = secrets.token_urlsafe(24)
    headers = _headers()
    forwarded = headers.get("x-forwarded-for") or headers.get("x-real-ip")
    return visitor_fingerprint(forwarded, st.session_state.mock_exam_browser_id)


def _token_from_url() -> str | None:
    value = st.query_params.get("exam")
    if isinstance(value, list):
        return value[0] if value else None
    return str(value) if value else None


def _session_from_url() -> dict[str, Any] | None:
    token = _token_from_url()
    if not token:
        return None
    try:
        session_id = verify_recovery_token(token)
    except InvalidRecoveryToken:
        st.warning("시험 링크가 만료되었거나 올바르지 않습니다.")
        return None
    session = get_session(session_id)
    if session is None:
        st.warning("이 시험 데이터는 보관 기간(72시간)이 지나 삭제되었습니다.")
        return None
    if session["status"] == "active":
        age = datetime.now(timezone.utc) - session["updated_at"]
        if age.total_seconds() > get_settings().MOCK_EXAM_RECOVERY_TTL_MINUTES * 60:
            st.warning("중단된 시험의 10분 복구 시간이 지났습니다. 새 시험은 일일 제한에 따라 시작할 수 있습니다.")
            return None
    return session


def _set_recovery_url(session_id: str) -> None:
    ttl = get_settings().MOCK_EXAM_RESULT_TTL_HOURS * 3600
    st.query_params["exam"] = sign_recovery_token(session_id, ttl_seconds=ttl)


def _render_information_panel(question) -> None:
    panel = question.information_panel
    if panel is None:
        return
    header_cells = "".join(f"<th>{html.escape(value)}</th>" for value in panel.headers)
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in row) + "</tr>"
        for row in panel.rows
    )
    note = f'<p class="mock-note">{html.escape(panel.note)}</p>' if panel.note else ""
    st.markdown(
        f"""
        <section class="mock-info">
          <h3>{html.escape(panel.title)}</h3>
          <p>{html.escape(panel.subtitle or '')}</p>
          <table><thead><tr>{header_cells}</tr></thead><tbody>{rows}</tbody></table>
          {note}
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_question_asset(exam, question) -> None:
    if question.asset_id:
        asset = next(asset for asset in exam.assets if asset.asset_id == question.asset_id)
        st.image(str(asset.source_url), use_container_width=True)
        st.caption(
            f"Photo: {asset.author} · {asset.license_name} · "
            f"[source]({asset.source_url}) · [license]({asset.license_url})"
        )
    _render_information_panel(question)


def _response_for_current(session_id: str, question_number: int) -> dict[str, Any] | None:
    return next(
        (row for row in list_responses(session_id) if row["question_number"] == question_number),
        None,
    )


def _render_active_exam(session: dict[str, Any]) -> None:
    exam = load_exam_set(session["exam_set_id"])
    number = int(session["current_question"])
    question = exam.questions[number - 1]
    st.progress((number - 1) / 11, text=f"Question {number} of 11")
    st.caption("Strict mode · no pause, back navigation, or re-recording · English only")
    st.subheader(f"Question {number}")
    st.markdown(f"**{question.direction}**")
    st.markdown(question.prompt)
    _render_question_asset(exam, question)

    existing = _response_for_current(session["id"], number)
    if existing and existing["status"] in {"queued", "processing"}:
        st.info("응답을 확인하고 있습니다. 다음 문항은 자동으로 열립니다.")
        time.sleep(1)
        st.rerun()
    if existing and existing["status"] == "technical_error":
        st.warning("녹음은 수신했지만 처리할 수 없었습니다. 이 문항에 한해 기술 재시도를 시작합니다.")

    retry_suffix = "retry" if session.get("retry_question") == number else "first"
    mic_attempt_key = f"mock_mic_attempt_{session['id']}_{number}_{retry_suffix}"
    attempt = int(st.session_state.get(mic_attempt_key, 0))
    run_id = f"{session['id']}:{number}:{retry_suffix}:{attempt}"
    with st.spinner("Piper 시험 음성을 준비하는 중..."):
        narration = narration_for(exam, question)
    if narration is None:
        st.caption("시험 음성 생성기를 사용할 수 없어 화면의 영어 문항을 기준으로 타이머를 진행합니다.")
    result = timed_recorder(
        run_id=run_id,
        preparation_seconds=question.preparation_seconds,
        response_seconds=question.response_seconds,
        group_read_seconds=question.group_read_seconds,
        prompt_audio=narration,
        prompt_repeat_count=question.prompt_repeat_count,
        key=f"timed_recorder_{run_id}",
    )
    if not result:
        st.caption("Chrome 또는 Edge에서 마이크 권한을 허용하세요. 타이머는 권한 확인 후 자동으로 시작합니다.")
        return
    if result.get("error"):
        st.error(f"마이크를 시작하지 못했습니다: {result['error']}")
        if st.button("기술 오류 해결 후 다시 연결", type="primary"):
            st.session_state[mic_attempt_key] = attempt + 1
            st.rerun()
        return
    try:
        audio_bytes = base64.b64decode(result["audio_base64"], validate=True)
        client_metrics = {
            "mime_type": result.get("mime_type"),
            "duration_seconds": result.get("duration_seconds"),
            "signal_detected": result.get("signal_detected"),
            "peak_rms": result.get("peak_rms"),
        }
        submit_response(
            session_id=session["id"],
            question_number=number,
            audio_bytes=audio_bytes,
            client_metrics=client_metrics,
        )
        st.rerun()
    except Exception as exc:
        st.error(f"응답을 저장하지 못했습니다: {type(exc).__name__}")


def _sample_report() -> dict[str, Any]:
    return {
        "score_low": 120,
        "score_high": 160,
        "expected_level": "Level 6–7 example",
        "confidence": "low",
        "scoring_profile": "basic",
        "scoring_version": "beta-1",
        "payload": {
            "method_note": "This is a sample layout, not a score for your speech.",
            "priority_improvements": [
                {
                    "question_number": 11,
                    "advice": "입장을 먼저 밝힌 뒤 이유와 구체적 예시를 연결하세요.",
                    "minimal_revision": "I think flexible hours benefit both sides because employees can work when they focus best.",
                    "band_example": "For example, a parent can begin earlier and still collaborate during core hours.",
                }
            ],
            "items": [],
        },
    }


def _render_report(report: dict[str, Any], *, session_id: str | None, sample: bool = False) -> None:
    payload = report.get("payload") or report
    if sample:
        st.info("동시 이용 또는 일일 제한 중에는 결과 화면 예시만 볼 수 있습니다.")
    if report.get("status") == "partial" or payload.get("partial_reason"):
        st.warning(payload.get("partial_reason") or "일부 평가 근거가 아직 처리 중인 임시 결과입니다.")
    st.subheader("Beta Score Estimate")
    left, middle, right = st.columns(3)
    left.metric("Estimated range", f"{report.get('score_low', 0)}–{report.get('score_high', 0)}")
    middle.metric("Expected level", report.get("expected_level") or "–")
    right.metric("Confidence", str(report.get("confidence") or "low").upper())
    st.caption(
        f"Profile: {report.get('scoring_profile', 'basic')} · Scoring version: "
        f"{report.get('scoring_version', 'beta-1')} · unofficial beta estimate"
    )
    st.markdown("### 우선 개선할 3가지")
    improvements = payload.get("priority_improvements") or []
    if not improvements:
        st.caption("상세 에이전트 피드백이 아직 준비되지 않았습니다.")
    for improvement in improvements[:3]:
        with st.expander(f"Q{improvement.get('question_number')} · {improvement.get('advice', '')}", expanded=True):
            if improvement.get("minimal_revision"):
                st.markdown(f"**Minimal revision:** {improvement['minimal_revision']}")
            if improvement.get("band_example"):
                st.markdown(f"**Band example:** {improvement['band_example']}")

    items = payload.get("items") or []
    if items:
        st.markdown("### 문항별 근거")
        for item in items:
            score = "–" if item.get("score") is None else f"{item['score']:.2f}/4"
            with st.expander(
                f"Q{item['question_number']} · {item.get('question_type', '').replace('_', ' ')} · {score}"
            ):
                if item.get("transcript"):
                    st.markdown(f"**Transcript:** {item['transcript']}")
                for evidence in item.get("evidence") or []:
                    st.markdown(f"- {evidence}")

    st.caption(payload.get("method_note", ""))
    if session_id and not sample:
        st.markdown("### 선택 사항: 실제 공식 점수 비교")
        st.caption("익명 보정 표본으로만 저장되며 30건 전에는 채점식에 반영하지 않습니다.")
        with st.form(f"calibration_{session_id}"):
            official = st.select_slider("공식 점수", options=list(range(0, 201, 10)), value=120)
            month = st.text_input("응시 월 (선택, YYYY-MM)", max_chars=7)
            submitted = st.form_submit_button("익명 표본 제출")
        if submitted:
            try:
                save_calibration_sample(
                    official_score=int(official),
                    official_exam_month=month.strip() or None,
                    report=report,
                )
                st.success("익명 보정 표본을 저장했습니다.")
            except ValueError as exc:
                st.error(str(exc))


def _render_scoring(session: dict[str, Any]) -> None:
    report = get_report(session["id"])
    if report and report["status"] in {"partial", "completed"}:
        _render_report(report, session_id=session["id"])
        if report["status"] == "partial" and st.button("상세 평가 상태 새로고침"):
            st.rerun()
        return
    started = session.get("completed_at") or session["updated_at"]
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    timeout = get_settings().MOCK_EXAM_SCORING_TIMEOUT_SECONDS
    if elapsed >= timeout:
        publish_timeout_report(session["id"])
        st.rerun()
    st.progress(min(0.95, elapsed / max(timeout, 1)), text="로컬 평가 에이전트가 근거를 검토하고 있습니다")
    st.caption(f"최대 {timeout}초 뒤에는 이용 가능한 근거로 임시 보고서를 먼저 공개합니다.")
    time.sleep(2)
    st.rerun()


def _render_intro(visitor_hash: str) -> None:
    st.title("Talky Speaking Mock Test")
    st.write("11개 문항을 약 20분 동안 실제 시험처럼 연속해서 답하는 영어 말하기 모의고사입니다.")
    st.warning("시험을 시작하면 타이머가 자동 진행되며 일시정지·뒤로가기·재녹음은 불가능합니다.")
    with st.expander("시험 전 확인", expanded=True):
        st.markdown(
            "- 한국어 안내 후 시험 화면은 영어로 진행됩니다.\n"
            "- 데스크톱 Chrome/Edge와 마이크, HTTPS 환경을 권장합니다.\n"
            "- 녹음은 암호화 저장되며 채점·기술 재시도·오류 분석에만 쓰고 72시간 뒤 삭제합니다.\n"
            "- 전사문과 보고서도 72시간 뒤 삭제하며 비식별 집계만 보관합니다.\n"
            "- 공개 데모 제한: IP 기준 하루 1회, 전체 동시 시험 1건."
        )
    st.caption(DISCLAIMER_KO)
    st.caption(DISCLAIMER_EN)
    status = quota_status(visitor_hash)
    if not status["allowed"]:
        reason = "오늘 이용 횟수를 모두 사용했습니다." if status["daily_used"] >= status["daily_limit"] else "다른 사용자가 시험을 진행 중입니다."
        st.info(reason)
        if st.button("결과 화면 예시 보기"):
            st.session_state.mock_show_sample = True
            st.rerun()
        if st.session_state.get("mock_show_sample"):
            _render_report(_sample_report(), session_id=None, sample=True)
        return
    consent = st.checkbox("개인정보 처리, 72시간 보관, 비공식 베타 채점에 동의합니다.")
    if st.button("모의고사 시작", type="primary", disabled=not consent):
        exam = list_approved_sets()[0]
        created = create_session(
            visitor_hash=visitor_hash,
            exam_set_id=exam.id,
            exam_set_version=exam.version,
        )
        _set_recovery_url(created["id"])
        st.rerun()


def render_mock_exam() -> None:
    st.markdown(
        """
        <style>
        .mock-info {border:1px solid #dce3e8;border-radius:12px;padding:16px;margin:10px 0 18px;background:#fafcfd}
        .mock-info h3,.mock-info p{margin:0 0 8px}.mock-info table{width:100%;border-collapse:collapse}
        .mock-info th,.mock-info td{border:1px solid #cfd8dc;padding:8px;text-align:left}
        .mock-info th{background:#eaf2f8}.mock-note{font-size:.9rem;margin-top:10px!important}
        </style>
        """,
        unsafe_allow_html=True,
    )
    if not st.session_state.get("mock_cleanup_done"):
        try:
            run_expiry_cleanup()
        finally:
            st.session_state.mock_cleanup_done = True
    session = _session_from_url()
    if session is None:
        _render_intro(_visitor_hash())
    elif session["status"] == "active":
        _render_active_exam(session)
    elif session["status"] in {"scoring", "completed"}:
        _render_scoring(session)
    else:
        st.error("시험을 계속할 수 없습니다. 데이터 보관 기간과 서버 상태를 확인해 주세요.")
    st.divider()
    st.caption(DISCLAIMER_KO)
    st.caption("Privacy: audio, transcripts, and reports are deleted after 72 hours. See PRIVACY.md and THIRD_PARTY_NOTICES.md.")
