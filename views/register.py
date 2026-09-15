"""The register: every job, filterable, with a triage view surfacing what's
urgent first. Reused for the full register (admin/principal) and for a
specialist's own queue (only_own=True). Every row is clickable — clicking a
Job ID opens its job detail page, which is the only place a job is updated."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import (
    CATEGORY_LABELS,
    RISK_AMBER,
    RISK_EMOJI,
    RISK_GREEN,
    RISK_GREY,
    RISK_RED,
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_DONE,
    STATUS_LABELS_SHORT,
    STATUSES_IN_ORDER,
    humanize,
)

RISK_ORDER = {RISK_RED: 0, RISK_AMBER: 1, RISK_GREY: 2, RISK_GREEN: 3}

# "Invoiced" isn't a literal job.status value — it's done + already on an
# invoice — so it's offered as a derived filter option rather than a new
# entry in the status enum/trigger.
_VIRTUAL_INVOICED = "invoiced"


def render(
    user: dict,
    *,
    only_own: bool = False,
    title: str = "Register",
    subtitle: str = "Every job, filterable — the single source of truth.",
    show_header: bool = True,
    category: str | None = None,
) -> None:
    if show_header:
        ui.page_header(title, subtitle)
    ui.risk_legend()
    key_prefix = "own" if only_own else (f"cat_{category}" if category else "reg")

    jobs = models.list_jobs(
        owner_id=user["id"] if only_own else None,
        category=category,
        exclude_dismissed=False,
    )

    _triage_section(jobs, key_prefix)
    st.divider()
    _filters_and_table(jobs, only_own, key_prefix, user)


def _triage_section(jobs: list, key_prefix: str) -> None:
    urgent = [j for j in jobs if j["status"] not in (STATUS_CLOSED, STATUS_DISMISSED)]
    urgent = [j for j in urgent if models.compute_risk(j) in (RISK_RED, RISK_AMBER)]
    urgent.sort(key=lambda j: (RISK_ORDER[models.compute_risk(j)], j["sla_date"] or j["created_at"].date()))

    st.markdown("#### Needs attention")
    if not urgent:
        st.caption("Nothing overdue, due soon, or blocked right now.")
        return
    ui.jobs_row_table(urgent[:10], key_prefix=f"{key_prefix}_triage")


def _filters_and_table(jobs: list, only_own: bool, key_prefix: str, user: dict) -> None:
    st.markdown("#### All jobs")

    # Every real status is always offered as a filter, whether or not any
    # job currently has it — plus the derived "Invoiced" option (done AND
    # already on an invoice, not a literal job.status value).
    status_labels = [STATUS_LABELS_SHORT[s] for s in STATUSES_IN_ORDER] + ["Invoiced"]
    label_to_status = dict(zip([STATUS_LABELS_SHORT[s] for s in STATUSES_IN_ORDER], STATUSES_IN_ORDER))
    label_to_status["Invoiced"] = _VIRTUAL_INVOICED

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        status_choice = st.multiselect(
            "Status", options=status_labels, key=f"{key_prefix}_f_status"
        )
    with col2:
        clients = sorted({j["client_name"] for j in jobs if j["client_name"]})
        client_choice = st.selectbox("Client", options=["All clients"] + clients, key=f"{key_prefix}_f_client")
    with col3:
        categories = sorted({j["category"] for j in jobs})
        cat_labels = ["All"] + [humanize(c, CATEGORY_LABELS) for c in categories]
        cat_choice = st.selectbox("Category", options=cat_labels, key=f"{key_prefix}_f_cat")
    with col4:
        owners = sorted({j["owner_name"] for j in jobs if j["owner_name"]})
        owner_choice = "All owners"
        if not only_own:
            owner_choice = st.selectbox("Owner", options=["All owners"] + owners, key=f"{key_prefix}_f_owner")

    search = st.text_input("Search job ID, title or client", key=f"{key_prefix}_f_search")
    show_dismissed = st.checkbox("Show dismissed items", value=False, key=f"{key_prefix}_f_dismissed")

    filtered = jobs
    if status_choice:
        wanted = {label_to_status[s] for s in status_choice}
        want_invoiced = _VIRTUAL_INVOICED in wanted
        real_wanted = wanted - {_VIRTUAL_INVOICED}
        filtered = [
            j for j in filtered
            if j["status"] in real_wanted
            or (want_invoiced and j["status"] == STATUS_DONE and j["invoice_id"])
        ]
    if not show_dismissed and not status_choice:
        filtered = [j for j in filtered if j["status"] != STATUS_DISMISSED]
    if client_choice != "All clients":
        filtered = [j for j in filtered if j["client_name"] == client_choice]
    if cat_choice != "All":
        filtered = [j for j in filtered if humanize(j["category"], CATEGORY_LABELS) == cat_choice]
    if owner_choice != "All owners":
        filtered = [j for j in filtered if j["owner_name"] == owner_choice]
    if search:
        s = search.lower()
        filtered = [
            j for j in filtered
            if s in (j["job_id"] or "").lower() or s in (j["title"] or "").lower() or s in (j["client_name"] or "").lower()
        ]

    filtered.sort(key=lambda j: (RISK_ORDER[models.compute_risk(j)], -j["created_at"].timestamp()))

    if user["role"] in (ROLE_ADMIN, ROLE_SUPER_ADMIN):
        _bulk_actions_table(filtered, key_prefix=f"{key_prefix}_all", user=user)
    else:
        ui.jobs_row_table(filtered, key_prefix=f"{key_prefix}_all")
    if filtered:
        st.caption(f"{len(filtered)} job(s)")


_BULK_TABLE_WIDTHS = [0.4, 0.4, 1.2, 1.6, 2.2, 1.2, 1.1, 1.0]
_BULK_TABLE_HEADERS = ["", "", "Job ID", "Client", "What", "Owner", "Status", "SLA date"]


def _bulk_actions_table(jobs: list, key_prefix: str, user: dict) -> None:
    """admin/super_admin: the same clickable job table everyone else sees,
    plus a per-row checkbox and bulk actions — assign an owner (both
    roles) and hide (super_admin only) — for acting on a batch of jobs at
    once instead of one at a time. Most useful right after a bulk import
    lands a run of unassigned New jobs in the register."""
    if not jobs:
        st.caption("Nothing here.")
        return

    select_key = f"{key_prefix}_selectall"
    prev_key = f"{key_prefix}_selectall_prev"
    select_all = st.checkbox("Select all", key=select_key)
    if st.session_state.get(prev_key) != select_all:
        for j in jobs:
            st.session_state[f"{key_prefix}_sel_{j['id']}"] = select_all
        st.session_state[prev_key] = select_all
        st.rerun()

    header_cols = st.columns(_BULK_TABLE_WIDTHS)
    for col, label in zip(header_cols, _BULK_TABLE_HEADERS):
        col.markdown(f"**{label}**")

    for j in jobs:
        cols = st.columns(_BULK_TABLE_WIDTHS)
        cols[0].checkbox(
            "", key=f"{key_prefix}_sel_{j['id']}", label_visibility="collapsed",
        )
        cols[1].write(RISK_EMOJI[models.compute_risk(j)])
        if cols[2].button(ui.short_job_id(j["job_id"]), key=f"{key_prefix}_row_{j['id']}", type="tertiary"):
            ui.go_to_job(j["id"])
        cols[3].write(j.get("client_name") or "—")
        cols[4].write(j["title"])
        cols[5].write(j.get("owner_name") or "—")
        cols[6].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        cols[7].write(j["sla_date"].isoformat() if j.get("sla_date") else "—")

    selected_ids = [j["id"] for j in jobs if st.session_state.get(f"{key_prefix}_sel_{j['id']}")]

    def _clear_selection() -> None:
        for jid in selected_ids:
            st.session_state.pop(f"{key_prefix}_sel_{jid}", None)
        st.session_state.pop(select_key, None)
        st.session_state.pop(prev_key, None)

    st.write("")
    staff = [s for s in models.list_staff(active_only=True) if s["role"] != "client"]
    staff_options = ["— choose owner —"] + [s["name"] for s in staff]
    staff_by_name = {s["name"]: s["id"] for s in staff}

    a1, a2 = st.columns([2, 1])
    with a1:
        owner_choice = st.selectbox(
            "Assign selected to", options=staff_options,
            key=f"{key_prefix}_assignto", label_visibility="collapsed",
        )
    with a2:
        if st.button(
            f"Assign selected ({len(selected_ids)})",
            disabled=not selected_ids or owner_choice == "— choose owner —",
            key=f"{key_prefix}_assignbtn", type="primary", use_container_width=True,
        ):
            changed = models.bulk_reassign_owner(selected_ids, staff_by_name[owner_choice], user["id"])
            _clear_selection()
            st.toast(f"Assigned {changed} job(s) to {owner_choice}.", icon="✅")
            st.rerun()

    if user["role"] == ROLE_SUPER_ADMIN:
        if st.button(
            f"Hide selected ({len(selected_ids)})", disabled=not selected_ids,
            key=f"{key_prefix}_hidebtn",
        ):
            models.hide_jobs(selected_ids, user["id"])
            _clear_selection()
            st.toast(f"Hid {len(selected_ids)} job(s).", icon="✅")
            st.rerun()
