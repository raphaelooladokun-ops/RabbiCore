"""CIT module dashboard: upcoming expiries (TCCs), recurring obligations due
by date, TCC jobs currently gated on outstanding obligations, who's assigned
to handle CIT work, and the register filtered to this pillar. Extends the
shared front office — capture, invoicing, notifications, comments and
expenses all keep working for CIT jobs exactly as before; this page adds
specialist depth on top, it doesn't replace any of it."""

from __future__ import annotations

import streamlit as st

from core import cit, models
from core import ui
from core.constants import ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN, STATUS_LABELS_SHORT, humanize
from views import register as register_view

_URGENCY_ICON = {"expired": "🔴", "due": "🟠", "approaching": "🟡"}


def render(user: dict) -> None:
    ui.page_header("CIT", "Recurring obligations, expiry tracking, and the TCC gate.")

    _upcoming_expiries()
    st.divider()
    _recurring_obligations()
    st.divider()
    _tcc_blocked_jobs()
    st.divider()

    if user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        _specialist_assignment()
        st.divider()

    register_view.render(user, title="CIT register", subtitle="Every CIT job.", category="cit")


def _upcoming_expiries() -> None:
    st.markdown("#### Upcoming expiries")
    st.caption("TCCs and any other CIT document with a validity date, across every client.")
    rows = models.list_upcoming_expiries(category="cit", within_days=90)
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


def _recurring_obligations() -> None:
    st.markdown("#### Upcoming recurring obligations")
    st.caption(
        "Monthly VAT Returns, VAT & WHT Monitoring, Yearly VAT Analysis, and Annual Return — "
        "marking one done automatically creates the next cycle, so this list is what's still due."
    )
    rows = models.list_recurring_jobs(category="cit")
    if not rows:
        st.caption("Nothing recurring outstanding right now.")
        return

    cols = st.columns([1.1, 2.0, 1.6, 1.2, 1.2, 1.1])
    for col, label in zip(cols, ["Job ID", "Client", "What", "Owner", "Status", "Due"]):
        col.markdown(f"**{label}**")

    for j in rows:
        row_cols = st.columns([1.1, 2.0, 1.6, 1.2, 1.2, 1.1])
        if row_cols[0].button(ui.short_job_id(j["job_id"]), key=f"rec_{j['id']}", type="tertiary"):
            ui.go_to_job(j["id"])
        row_cols[1].write(j.get("client_name") or "—")
        row_cols[2].write(j["title"])
        row_cols[3].write(j.get("owner_name") or "—")
        row_cols[4].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        row_cols[5].write(j["sla_date"].isoformat() if j.get("sla_date") else "—")


def _tcc_blocked_jobs() -> None:
    st.markdown("#### Blocked on outstanding obligations")
    blocked = cit.list_tcc_jobs_blocked()
    if not blocked:
        st.caption("No TCC jobs currently blocked on outstanding CIT obligations.")
        return

    for job, outstanding in blocked:
        cols = st.columns([1.2, 2.6, 1.2])
        if cols[0].button(ui.short_job_id(job["job_id"]), key=f"tccb_{job['id']}", type="tertiary"):
            ui.go_to_job(job["id"])
        cols[1].write(f"{job['client_name'] or '—'} — {job['title']}")
        cols[2].write(job["owner_name"] or "—")
        names = ", ".join(f"{o['job_id']} ({o['status']})" for o in outstanding)
        st.caption(f"Waiting on: {names}")


def _specialist_assignment() -> None:
    st.markdown("#### CIT specialists")
    st.caption(
        "Staff assigned here float to the top (and are pre-selected) as Owner once a CIT "
        "service is picked in Capture — anyone can still be assigned, this is routing help, not a restriction."
    )
    assigned = models.list_module_specialists("cit")
    assigned_ids = {a["staff_id"] for a in assigned}

    if assigned:
        for a in assigned:
            c1, c2 = st.columns([3, 1])
            c1.write(a["staff_name"])
            if c2.button("Remove", key=f"rmspec_{a['id']}"):
                models.unassign_module_specialist("cit", a["staff_id"])
                st.rerun()
    else:
        st.caption("No specialists assigned yet — anyone can still own a CIT job.")

    candidates = [
        s for s in models.list_staff(active_only=True)
        if s["role"] == "specialist" and s["id"] not in assigned_ids
    ]
    if candidates:
        staff_map = {s["name"]: s for s in candidates}
        col1, col2 = st.columns([3, 1])
        choice = col1.selectbox(
            "Add specialist", options=list(staff_map.keys()), index=None,
            placeholder="Select…", key="addspec_cit", label_visibility="collapsed",
        )
        if col2.button("Add", key="addspec_btn_cit") and choice:
            models.assign_module_specialist("cit", staff_map[choice]["id"])
            st.rerun()
