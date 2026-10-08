"""Principal overview: the whole book at a glance — what's stalled, what's
blocked, what's done-but-unbilled, without asking anyone. Status counts are
clickable and open the list of jobs behind that count."""

from __future__ import annotations

import streamlit as st

from core import models
from core import tz
from core import ui
from core.constants import RISK_RED, ROLE_PRINCIPAL, STATUS_LABELS_SHORT, humanize, titlecase_name

STATUS_ORDER = ["new", "in_progress", "blocked", "done", "closed"]
STATUS_FILTER_KEY = "principal_status_filter"


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {titlecase_name(user['name']).split()[0]}", "Firm-wide oversight — every job, every client.")
    ui.risk_legend()

    # Every role that lands on Overview (principal, manager, super_admin)
    # is one of the three roles that can resolve an EC action point, so no
    # extra role check is needed here — pinned above everything else,
    # oldest first, exactly like the escalated queue it is.
    _ec_action_queue(user)

    summary = models.firm_summary()
    jobs = models.list_jobs(exclude_dismissed=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open jobs", summary["total"] - summary["by_status"].get("closed", 0) - summary["by_status"].get("dismissed", 0))
    c2.metric("Stalled >48h", summary["stalled"])
    c3.metric("Done, unbilled", summary["done_unbilled"])
    c4.metric("Red flags", summary["red_flags"])

    st.write("")
    st.markdown("#### By status — click a count to see those jobs")
    status_cols = st.columns(len(STATUS_ORDER))
    for col, s in zip(status_cols, STATUS_ORDER):
        count = summary["by_status"].get(s, 0)
        label = f"{STATUS_LABELS_SHORT.get(s, humanize(s))} ({count})"
        active = st.session_state.get(STATUS_FILTER_KEY) == s
        if col.button(label, key=f"statusbtn_{s}", type="primary" if active else "secondary", use_container_width=True):
            st.session_state[STATUS_FILTER_KEY] = None if active else s
            st.rerun()

    active_status = st.session_state.get(STATUS_FILTER_KEY)
    if active_status:
        st.write("")
        st.caption(f"Jobs — {STATUS_LABELS_SHORT.get(active_status, humanize(active_status))}")
        ui.jobs_row_table(
            [j for j in jobs if j["status"] == active_status],
            key_prefix=f"principal_status_{active_status}",
        )

    st.write("")
    st.markdown("#### Red-flag jobs")
    red = [j for j in jobs if models.compute_risk(j) == RISK_RED]
    ui.jobs_row_table(red, key_prefix="principal_redflag")

    st.write("")
    st.markdown("#### Stalled — no movement in 48h+")
    stalled = [j for j in jobs if models.is_stalled(j)]
    ui.jobs_row_table(stalled, key_prefix="principal_stalled")

    st.write("")
    st.markdown("#### Done — awaiting invoice")
    unbilled = [
        j for j in jobs
        if j["status"] == "done" and j["invoice_id"] is None and not models.is_recurring_parent_job(j)
    ]
    ui.jobs_row_table(unbilled, key_prefix="principal_unbilled")

    # Manager and super_admin also have a separate Home page (home_admin.py)
    # that carries the feed — showing it here too would just be the same
    # thing twice. Principal has no Home page at all, so Overview is the
    # only place they'll ever see it.
    if user["role"] == ROLE_PRINCIPAL:
        st.write("")
        ui.updates_feed()


def _ec_action_queue(user: dict) -> None:
    """Every open EC action point, oldest first, each showing how long
    it's been waiting — resolving needs a short decision note and never
    touches the job's own status, only the flag."""
    open_points = models.list_open_ec_action_points()
    if not open_points:
        return
    st.markdown(f"#### 📌 EC action points — {len(open_points)} open")
    st.caption("Oldest first. Resolving requires a short decision note and never changes the job's own status.")
    for c in open_points:
        age = models.format_duration(c["action_opened_at"], tz.now_utc())
        with st.container(border=True):
            st.markdown(f'<span class="rc-badge rc-badge-amber">waiting {age}</span>', unsafe_allow_html=True)
            if st.button(
                f"{ui.short_job_id(c['job_code'])} — {titlecase_name(c['client_name']) or '—'}: {c['job_title']}",
                key=f"ecq_open_{c['id']}", type="tertiary",
            ):
                ui.go_to_job(c["job_pk"])
            st.write(f"**{titlecase_name(c['author_name'])}:** {c['body']}")
            with st.form(key=f"ecq_resolve_{c['id']}"):
                note = st.text_input("Resolution — what's the decision? *", key=f"ecq_note_{c['id']}")
                if st.form_submit_button("Resolve"):
                    if not note.strip():
                        st.error("Enter the decision or answer before resolving.")
                    else:
                        models.resolve_ec_action_point(c["id"], user["id"], note.strip())
                        st.toast("Resolved.", icon="✅")
                        st.rerun()
    st.write("")
    st.divider()
