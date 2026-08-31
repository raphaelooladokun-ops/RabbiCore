from __future__ import annotations

import streamlit as st

from core.auth import login, verify_login


def render() -> None:
    st.markdown(
        '<div class="rc-login-wrap">'
        '<div class="rc-login-wordmark">RABBI CORE</div>'
        '<div class="rc-login-tagline">Front office — sign in to your account</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True)

        if submitted:
            if not email or not password:
                st.error("Enter your email and password.")
                return
            user = verify_login(email.strip(), password)
            if user is None:
                st.error("Incorrect email or password.")
                return
            login(user)
            st.rerun()
