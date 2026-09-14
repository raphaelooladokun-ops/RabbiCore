"""Rabbi Core — entrypoint. A thin Streamlit layer over the Neon data model:
gate on login, then route to the screens each role is allowed to see."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.auth import current_user, logout, restore_session_from_query_params
from core.bootstrap import bootstrap_once
from core.constants import (
    CATEGORY_LABELS,
    ROLE_ADMIN,
    ROLE_CLIENT,
    ROLE_LABELS,
    ROLE_PRINCIPAL,
    ROLE_SPECIALIST,
    ROLE_SUPER_ADMIN,
)
from core.cit import sync_tcc_gate
from core.immigration import sync_quota_cerpac_gate
from views import (
    billing,
    capture,
    cit,
    hidden_jobs,
    home_admin,
    home_client,
    home_principal,
    home_specialist,
    immigration,
    invoice_create,
    invoice_detail,
    job_detail,
    login,
    register,
    users,
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


@st.cache_resource(ttl=300, show_spinner=False)
def _quota_gate_sweep_ticket() -> bool:
    """Same pattern as the SLA sweep: re-checks every CERPAC job linked to a
    quota position at most once every 5 minutes, so a validity window
    crossing the 6-month line purely with time passing still gets caught
    even if nobody touches either job's record."""
    sync_quota_cerpac_gate()
    return True


_quota_gate_sweep_ticket()


@st.cache_resource(ttl=300, show_spinner=False)
def _tcc_gate_sweep_ticket() -> bool:
    """Same pattern: re-checks every open TCC job at most once every 5
    minutes, so a client's obligations clearing (or a new one appearing)
    is caught even if nobody happened to touch the TCC job itself."""
    sync_tcc_gate()
    return True


_tcc_gate_sweep_ticket()

NAV = {
    ROLE_PRINCIPAL: [
        ("Overview", home_principal.render),
        ("Capture", capture.render),
        ("Register", lambda u: register.render(u)),
        ("Immigration", immigration.render),
        ("CIT", cit.render),
        ("Billing", billing.render),
    ],
    ROLE_ADMIN: [
        ("Home", home_admin.render),
        ("Capture", capture.render),
        ("Register", lambda u: register.render(u)),
        ("Immigration", immigration.render),
        ("CIT", cit.render),
        ("Billing", billing.render),
    ],
    ROLE_SPECIALIST: [
        ("My Queue", home_specialist.render),
    ],
    ROLE_CLIENT: [
        ("My Jobs", home_client.render),
    ],
    # Full access — the union of every other role's environment, so the
    # firm owner can see and act on all of it from one account.
    ROLE_SUPER_ADMIN: [
        ("Overview", home_principal.render),
        ("Home", home_admin.render),
        ("Capture", capture.render),
        ("Register", lambda u: register.render(u)),
        ("Immigration", immigration.render),
        ("CIT", cit.render),
        ("Billing", billing.render),
        ("Users", users.render),
        ("Hidden Jobs", hidden_jobs.render),
        ("My Queue", home_specialist.render),
        ("My Jobs", home_client.render),
    ],
}


def main() -> None:
    restore_session_from_query_params()
    user = current_user()

    if user is None:
        login.render()
        return

    pages = NAV[user["role"]]
    page_names = [name for name, _ in pages]

    # Restores the page/job/invoice the user was on before a hard reload —
    # a no-op after the first run of this session_state, since navigation
    # from here on keeps the URL in sync itself (see sync_nav_query_params
    # below).
    ui.restore_nav_from_query_params(page_names)

    ui.sidebar_wordmark()
    st.sidebar.markdown(f"**{user['name']}**")
    role_caption = ROLE_LABELS[user["role"]]
    specialities = models.list_staff_categories().get(user["id"], [])
    if specialities:
        role_caption += " — " + ", ".join(CATEGORY_LABELS.get(c, c) for c in specialities)
    st.sidebar.caption(role_caption)
    if user["role"] == ROLE_SUPER_ADMIN:
        ui.super_admin_badge()
    ui.notification_bell(user)
    st.sidebar.write("")

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

    ui.sync_nav_query_params()

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
