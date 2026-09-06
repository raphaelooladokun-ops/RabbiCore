"""Immigration module dashboard: upcoming expiries across every immigration
job, jobs currently blocked on the quota->CERPAC gate, who's assigned to
handle immigration work, and the register filtered to this pillar. Extends
the shared front office — capture, invoicing, notifications, comments and
expenses all keep working for immigration jobs exactly as before; this page
adds specialist depth on top, it doesn't replace any of it."""

from __future__ import annotations

import streamlit as st

from core import immigration, models
from core import ui
from core.constants import ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN
from views import register as register_view

_URGENCY_ICON = {"expired": "🔴", "due": "🟠", "approaching": "🟡"}


def render(user: dict) -> None:
    ui.page_header("Immigration", "Document readiness, expiry tracking, and the quota → CERPAC gate.")

    _upcoming_expiries()
    st.divider()
    _quota_blocked_jobs()
    st.divider()

    if user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        _specialist_assignment()
        st.divider()

    register_view.render(
        user, title="Immigration register", subtitle="Every immigration job.", category="immigration",
    )


def _upcoming_expiries() -> None:
    st.markdown("#### Upcoming expiries")
    rows = models.list_upcoming_expiries(category="immigration", within_days=90)
    if not rows:
        st.caption("Nothing expiring in the next 90 days.")
        return

    cols = st.columns([0.4, 1.1, 2.2, 1.8, 1.2, 1.3])
    for col, label in zip(cols, ["", "Job ID", "Document", "What", "Owner", "Expiry"]):
        col.markdown(f"**{label}**")

    for r in rows:
        urgency = models.expiry_urgency(r["expiry_date"])
        row_cols = st.columns([0.4, 1.1, 2.2, 1.8, 1.2, 1.3])
        row_cols[0].write(_URGENCY_ICON[urgency])
        if row_cols[1].button(ui.short_job_id(r["job_code"]), key=f"exp_{r['job_document_id']}", type="tertiary"):
            ui.go_to_job(r["job_pk"])
        row_cols[2].write(f"{r['document_name']} — {r['client_name'] or '—'}")
        row_cols[3].write(r["title"])
        row_cols[4].write(r["owner_name"] or "—")
        row_cols[5].write(f"{models.EXPIRY_URGENCY_LABELS[urgency]} · {r['expiry_date'].isoformat()}")


def _quota_blocked_jobs() -> None:
    st.markdown("#### Blocked on quota renewal")
    linked = immigration.list_cerpac_jobs_with_quota_link()
    blocked = [(job, quota) for job, quota in linked if immigration.is_quota_gate_active(job)]
    if not blocked:
        st.caption("No CERPAC jobs currently blocked on a quota renewal.")
        return

    for job, quota in blocked:
        cols = st.columns([1.2, 2.6, 1.8, 1.2])
        if cols[0].button(ui.short_job_id(job["job_id"]), key=f"qb_{job['id']}", type="tertiary"):
            ui.go_to_job(job["id"])
        cols[1].write(f"{job['client_name'] or '—'} — {job['title']}")
        if quota:
            if cols[2].button(f"Quota: {ui.short_job_id(quota['job_id'])}", key=f"qbq_{job['id']}", type="tertiary"):
                ui.go_to_job(quota["id"])
        else:
            cols[2].write("—")
        cols[3].write(job["owner_name"] or "—")


def _specialist_assignment() -> None:
    st.markdown("#### Immigration specialists")
    st.caption(
        "Staff assigned here float to the top (and are pre-selected) as Owner once an immigration "
        "service is picked in Capture — anyone can still be assigned, this is routing help, not a restriction."
    )
    assigned = models.list_module_specialists("immigration")
    assigned_ids = {a["staff_id"] for a in assigned}

    if assigned:
        for a in assigned:
            c1, c2 = st.columns([3, 1])
            c1.write(a["staff_name"])
            if c2.button("Remove", key=f"rmspec_{a['id']}"):
                models.unassign_module_specialist("immigration", a["staff_id"])
                st.rerun()
    else:
        st.caption("No specialists assigned yet — anyone can still own an immigration job.")

    candidates = [
        s for s in models.list_staff(active_only=True)
        if s["role"] == "specialist" and s["id"] not in assigned_ids
    ]
    if candidates:
        staff_map = {s["name"]: s for s in candidates}
        col1, col2 = st.columns([3, 1])
        choice = col1.selectbox(
            "Add specialist", options=list(staff_map.keys()), index=None,
            placeholder="Select…", key="addspec_immigration", label_visibility="collapsed",
        )
        if col2.button("Add", key="addspec_btn_immigration") and choice:
            models.assign_module_specialist("immigration", staff_map[choice]["id"])
            st.rerun()
