import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:11434/v1")
API_KEY = os.getenv("API_KEY", "ollama")
MODEL_NAME = os.getenv("MODEL_NAME", "skt/A.X-4.0-Light")

SYSTEM_PROMPT = """
You are a friendly English conversation tutor. Follow these rules:

1. Always respond in English only.
2. Keep the conversation natural and casual, like talking to a friend.
3. If the user makes a grammar or vocabulary mistake, gently correct it by saying something like: "By the way, a more natural way to say that would be: ..."
4. After correcting, continue the conversation naturally — don't just stop at the correction.
5. Adjust your language complexity to match the user's level.
6. Ask follow-up questions to keep the conversation going.
7. If the user writes in Korean, kindly translate in English.
"""

client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)

st.set_page_config(page_title="Talky")
st.title("Talky - English Conversation AI Agent")

with st.sidebar:
    st.markdown("### Settings")
    st.text(f"Model: {MODEL_NAME}")
    st.text(f"Server: {API_BASE_URL}")
    if st.button("🔄 New Conversation"):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("영어/한국어로 입력해주세요."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + st.session_state.messages
        stream = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            stream=True,
        )
        response = st.write_stream(
            token.choices[0].delta.content or ""
            for token in stream
            if token.choices[0].delta.content is not None
        )
    st.session_state.messages.append({"role": "assistant", "content": response})
