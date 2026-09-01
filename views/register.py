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
    RISK_GREEN,
    RISK_GREY,
    RISK_RED,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_LABELS_SHORT,
    humanize,
)

RISK_ORDER = {RISK_RED: 0, RISK_AMBER: 1, RISK_GREY: 2, RISK_GREEN: 3}


def render(
    user: dict,
    *,
    only_own: bool = False,
    title: str = "Register",
    subtitle: str = "Every job, filterable — the single source of truth.",
    show_header: bool = True,
) -> None:
    if show_header:
        ui.page_header(title, subtitle)
    key_prefix = "own" if only_own else "reg"

    jobs = models.list_jobs(
        owner_id=user["id"] if only_own else None,
        exclude_dismissed=False,
    )

    _triage_section(jobs, key_prefix)
    st.divider()
    _filters_and_table(jobs, only_own, key_prefix)


def _triage_section(jobs: list, key_prefix: str) -> None:
    urgent = [j for j in jobs if j["status"] not in (STATUS_CLOSED, STATUS_DISMISSED)]
    urgent = [j for j in urgent if models.compute_risk(j) in (RISK_RED, RISK_AMBER)]
    urgent.sort(key=lambda j: (RISK_ORDER[models.compute_risk(j)], j["sla_date"] or j["created_at"].date()))

    st.markdown("#### Needs attention")
    if not urgent:
        st.caption("Nothing overdue, due soon, or blocked right now.")
        return
    ui.jobs_row_table(urgent[:10], key_prefix=f"{key_prefix}_triage")


def _filters_and_table(jobs: list, only_own: bool, key_prefix: str) -> None:
    st.markdown("#### All jobs")

    statuses_present = sorted({j["status"] for j in jobs})
    status_labels = [STATUS_LABELS_SHORT.get(s, humanize(s)) for s in statuses_present]
    label_to_status = dict(zip(status_labels, statuses_present))

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
        filtered = [j for j in filtered if j["status"] in wanted]
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

    ui.jobs_row_table(filtered, key_prefix=f"{key_prefix}_all")
    if filtered:
        st.caption(f"{len(filtered)} job(s)")
