import streamlit as st

from app.auth import authenticate, register_user


def require_user() -> str:
    user_id = st.session_state.get("auth_user_id")
    if user_id:
        with st.sidebar:
            if st.button("로그아웃"):
                st.session_state.clear()
                st.rerun()
        return user_id
    st.title("Talky 로그인")
    login, signup = st.tabs(["로그인", "계정 만들기"])
    with login, st.form("login"):
        name = st.text_input("아이디", max_chars=64)
        password = st.text_input("비밀번호", type="password", max_chars=128)
        if st.form_submit_button("로그인"):
            authenticated = authenticate(name, password)
            if authenticated:
                st.session_state.clear()
                st.session_state.auth_user_id = authenticated
                st.rerun()
            st.error("로그인할 수 없습니다. 계정 정보를 확인하거나 잠시 후 다시 시도하세요.")
    with signup, st.form("signup"):
        name = st.text_input("새 아이디", max_chars=64)
        password = st.text_input("새 비밀번호 (12자 이상)", type="password", max_chars=128)
        repeated = st.text_input("비밀번호 확인", type="password", max_chars=128)
        if st.form_submit_button("계정 만들기"):
            try:
                if password != repeated:
                    raise ValueError("비밀번호가 일치하지 않습니다.")
                register_user(name, password)
                st.success("계정을 만들었습니다. 로그인해 주세요.")
            except ValueError as exc:
                st.error(str(exc))
    st.stop()
