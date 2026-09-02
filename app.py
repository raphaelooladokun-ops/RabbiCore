"""Rabbi Core — entrypoint. A thin Streamlit layer over the Neon data model:
gate on login, then route to the screens each role is allowed to see."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.auth import current_user, logout
from core.bootstrap import bootstrap_once
from core.constants import ROLE_ADMIN, ROLE_CLIENT, ROLE_LABELS, ROLE_PRINCIPAL, ROLE_SPECIALIST
from views import (
    billing,
    capture,
    home_admin,
    home_client,
    home_principal,
    home_specialist,
    invoice_create,
    invoice_detail,
    job_detail,
    login,
    register,
)

st.set_page_config(page_title="Rabbi Core", page_icon="📋", layout="wide")

ui.inject_theme()
bootstrap_once()


@st.cache_resource(ttl=300, show_spinner=False)
def _sla_sweep_ticket() -> bool:
    """Runs the SLA notification sweep at most once every 5 minutes across
    every session sharing this process — SLA due/overdue only ever changes
    at day granularity, so this stays cheap without ever going stale enough
    to matter."""
    models.sync_sla_notifications()
    return True


_sla_sweep_ticket()

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
    ui.notification_bell(user)
    st.sidebar.write("")

    page_names = [name for name, _ in pages]
    choice = st.session_state.get("_current_page")
    if choice not in page_names:
        choice = page_names[0]
        st.session_state["_current_page"] = choice

    in_detail_view = bool(
        st.session_state.get(ui.NAV_JOB_KEY)
        or st.session_state.get(ui.NAV_INVOICE_KEY)
        or st.session_state.get(ui.NAV_CREATE_INVOICE_KEY)
    )

    # Real buttons, not a radio: a click always reruns even when the page is
    # already "selected", so clicking a sidebar item reliably exits a job or
    # invoice detail view back to that list — a radio's unchanged value
    # would silently no-op in that exact situation.
    for name in page_names:
        active = name == choice and not in_detail_view
        if st.sidebar.button(
            name, key=f"navbtn_{name}", type="primary" if active else "secondary", use_container_width=True
        ):
            st.session_state["_current_page"] = name
            ui.clear_all_nav()
            st.rerun()

    st.sidebar.write("")
    st.sidebar.divider()
    if st.sidebar.button("Log out", use_container_width=True):
        logout()
        st.rerun()

    if st.session_state.get(ui.NAV_CREATE_INVOICE_KEY):
        invoice_create.render(user)
        return

    nav_invoice_pk = st.session_state.get(ui.NAV_INVOICE_KEY)
    if nav_invoice_pk:
        invoice_detail.render(user, nav_invoice_pk)
        return

    nav_job_pk = st.session_state.get(ui.NAV_JOB_KEY)
    if nav_job_pk:
        job_detail.render(user, nav_job_pk)
        return

    render_fn = dict(pages)[choice]
    render_fn(user)


if __name__ == "__main__":
    main()
