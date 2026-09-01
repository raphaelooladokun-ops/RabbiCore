"""Rabbi Core — entrypoint. A thin Streamlit layer over the Neon data model:
gate on login, then route to the screens each role is allowed to see."""

from __future__ import annotations

import streamlit as st

from core import ui
from core.auth import current_user, logout
from core.bootstrap import bootstrap_once
from core.constants import ROLE_ADMIN, ROLE_CLIENT, ROLE_LABELS, ROLE_PRINCIPAL, ROLE_SPECIALIST
from views import billing, capture, home_admin, home_client, home_principal, home_specialist, job_detail, login, register

st.set_page_config(page_title="Rabbi Core", page_icon="📋", layout="wide")

ui.inject_theme()
bootstrap_once()

NAV = {
    ROLE_PRINCIPAL: [
        ("Overview", home_principal.render),
        ("Capture", capture.render),
        ("Register", lambda u: register.render(u)),
        ("Billing", billing.render),
    ],
    ROLE_ADMIN: [
        ("Home", home_admin.render),
        ("Capture", capture.render),
        ("Register", lambda u: register.render(u)),
        ("Billing", billing.render),
    ],
    ROLE_SPECIALIST: [
        ("My Queue", home_specialist.render),
    ],
    ROLE_CLIENT: [
        ("My Jobs", home_client.render),
    ],
}


def main() -> None:
    user = current_user()

    if user is None:
        login.render()
        return

    pages = NAV[user["role"]]

    ui.sidebar_wordmark()
    st.sidebar.markdown(f"**{user['name']}**")
    st.sidebar.caption(ROLE_LABELS[user["role"]])
    st.sidebar.write("")

    page_names = [name for name, _ in pages]
    choice = st.sidebar.radio("Navigate", page_names, label_visibility="collapsed", key="nav_choice")

    if st.session_state.get("_last_nav_choice") != choice:
        st.session_state["_last_nav_choice"] = choice
        ui.clear_job_nav()

    st.sidebar.write("")
    st.sidebar.divider()
    if st.sidebar.button("Log out", use_container_width=True):
        logout()
        st.rerun()

    nav_job_pk = st.session_state.get(ui.NAV_JOB_KEY)
    if nav_job_pk:
        job_detail.render(user, nav_job_pk)
        return

    render_fn = dict(pages)[choice]
    render_fn(user)


if __name__ == "__main__":
    main()
