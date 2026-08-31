"""The register: every job, filterable, with a triage view surfacing what's
urgent first. Reused for the full register (admin/principal) and for a
specialist's own queue (only_own=True)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import models
from core import ui
from core.constants import (
    CATEGORY_LABELS,
    RISK_GREY,
    RISK_RED,
    RISK_AMBER,
    RISK_GREEN,
    SOURCE_LABELS,
    STATUS_BLOCKED,
    STATUS_CLOSED,
    STATUS_DISMISSED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_LABELS,
    STATUS_LABELS_SHORT,
    STATUS_NEW,
    humanize,
)

RISK_EMOJI = {RISK_RED: "🔴", RISK_AMBER: "🟠", RISK_GREEN: "🟢", RISK_GREY: "⚪"}
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
    filtered = _filters_and_table(jobs, user, only_own, key_prefix)
    st.divider()
    _update_section(filtered, user, editable=True, key_prefix=key_prefix)


def _triage_section(jobs: list, key_prefix: str) -> None:
    urgent = [j for j in jobs if j["status"] not in (STATUS_CLOSED, STATUS_DISMISSED)]
    urgent = [j for j in urgent if models.compute_risk(j) in (RISK_RED, RISK_AMBER)]
    urgent.sort(key=lambda j: (RISK_ORDER[models.compute_risk(j)], j["sla_date"] or j["created_at"].date()))

    st.markdown("#### Needs attention")
    if not urgent:
        st.caption("Nothing overdue, due soon, or blocked right now.")
        return

    rows = []
    for j in urgent[:10]:
        rows.append(
            {
                "": RISK_EMOJI[models.compute_risk(j)],
                "Job ID": j["job_id"],
                "Client": j["client_name"],
                "What": j["title"],
                "Status": STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])),
                "SLA date": j["sla_date"].isoformat() if j["sla_date"] else "—",
                "Owner": j["owner_name"] or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _filters_and_table(jobs: list, user: dict, only_own: bool, key_prefix: str) -> list:
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

    if not filtered:
        st.caption("No jobs match these filters.")
        return filtered

    rows = []
    for j in filtered:
        rows.append(
            {
                "": RISK_EMOJI[models.compute_risk(j)],
                "Job ID": j["job_id"],
                "Client": j["client_name"],
                "Service": j["service_name"] or "—",
                "Owner": j["owner_name"] or "—",
                "Status": STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])),
                "SLA date": j["sla_date"].isoformat() if j["sla_date"] else "—",
                "Invoice": j["invoice_code"] or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(f"{len(filtered)} job(s)")
    return filtered


def _update_section(filtered: list, user: dict, editable: bool, key_prefix: str) -> None:
    st.markdown("#### Update a job")
    if not filtered:
        st.caption("No jobs to update with the current filters.")
        return

    options = {f"{j['job_id']} — {j['client_name']} — {j['title']}": j for j in filtered}
    pick = st.selectbox("Select a job", options=list(options.keys()), key=f"{key_prefix}_pick", index=None,
                         placeholder="Choose a job to view or update…")
    if not pick:
        return
    job = models.get_job(options[pick]["id"])
    _job_detail_card(job, user, editable, key_prefix)


def _job_detail_card(job: dict, user: dict, editable: bool, key_prefix: str) -> None:
    with st.container(border=True):
        badges = ui.status_badge_html(job["status"]) + " " + ui.risk_badge_html(models.compute_risk(job))
        st.markdown(f"### {job['job_id']} &nbsp; {badges}", unsafe_allow_html=True)
        st.markdown(f"**{job['title']}**")

        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**Client:** {job['client_name']}")
            st.write(f"**Service:** {job['service_name'] or '—'}")
            st.write(f"**Category:** {humanize(job['category'], CATEGORY_LABELS)}")
            st.write(f"**Owner:** {job['owner_name'] or '—'}")
            st.write(f"**Source:** {humanize(job['source'], SOURCE_LABELS)}")
        with c2:
            st.write(f"**Logged:** {job['created_at'].strftime('%d %b %Y')}")
            st.write(f"**SLA date:** {job['sla_date'].isoformat() if job['sla_date'] else '—'}")
            st.write(f"**Invoice:** {job['invoice_code'] or 'Not invoiced'}")
            if job["blocked_by"]:
                blocker_state = "resolved" if not models.is_actually_blocked(job) else "unresolved"
                st.write(f"**Depends on:** {job['blocked_by_job_code']} ({blocker_state})")

        attrs = models.get_job_extension(job["id"])
        if attrs:
            service = models.get_service(job["service_type"]) if job["service_type"] else None
            field_labels = {f["key"]: f["label"] for f in (service["fields"] if service else [])}
            st.write("**Details:** " + " · ".join(f"{field_labels.get(k, k)}: {v}" for k, v in attrs.items()))

        if job["description"]:
            st.write(f"**Description:** {job['description']}")
        if job["waiting_on_client"]:
            st.info(f"**Waiting on client:** {job['waiting_on_client']}")
        if job["dismissed_reason"]:
            st.write(f"**Dismissed — reason:** {job['dismissed_reason']}")
        if job["internal_notes"]:
            st.caption(f"Internal notes: {job['internal_notes']}")

        if not editable or job["status"] in (STATUS_CLOSED, STATUS_DISMISSED):
            return

        st.divider()
        _actions(job, key_prefix)


def _actions(job: dict, key_prefix: str) -> None:
    blocked_now = models.is_actually_blocked(job)

    if blocked_now:
        st.warning(f"Blocked by **{job['blocked_by_job_code']}** — resolve that job first.")
    else:
        cols = st.columns(4)
        if job["status"] == STATUS_NEW:
            if cols[0].button("Start work", key=f"{key_prefix}_start_{job['id']}"):
                _apply_status(job["id"], STATUS_IN_PROGRESS)
        if job["status"] == STATUS_IN_PROGRESS:
            if cols[0].button("Mark done", key=f"{key_prefix}_done_{job['id']}"):
                _apply_status(job["id"], STATUS_DONE)
            if cols[1].button("Mark blocked", key=f"{key_prefix}_block_{job['id']}"):
                _apply_status(job["id"], STATUS_BLOCKED)
        if job["status"] == STATUS_BLOCKED and not job["blocked_by"]:
            if cols[0].button("Resume — in progress", key=f"{key_prefix}_resume_{job['id']}"):
                _apply_status(job["id"], STATUS_IN_PROGRESS)

    st.write("")
    with st.expander("Dependency, notes & waiting-on-client"):
        _dependency_control(job, key_prefix)
        notes = st.text_area("Internal notes", value=job["internal_notes"] or "", key=f"{key_prefix}_notes_{job['id']}")
        waiting = st.text_input(
            "Waiting on client for…", value=job["waiting_on_client"] or "", key=f"{key_prefix}_waiting_{job['id']}"
        )
        if st.button("Save notes", key=f"{key_prefix}_savenotes_{job['id']}"):
            models.update_job_fields(job["id"], internal_notes=notes or None, waiting_on_client=waiting or None)
            st.toast("Saved.", icon="✅")
            st.rerun()


def _dependency_control(job: dict, key_prefix: str) -> None:
    candidates = [
        j for j in models.list_jobs(exclude_dismissed=True)
        if j["id"] != job["id"] and j["client_id"] == job["client_id"]
    ]
    options = {"— no dependency —": None}
    options.update({f"{c['job_id']} — {c['title']}": c["id"] for c in candidates})

    current_label = "— no dependency —"
    if job["blocked_by"]:
        for label, pk in options.items():
            if pk == job["blocked_by"]:
                current_label = label
                break

    choice = st.selectbox(
        "Blocked by (dependency)", options=list(options.keys()),
        index=list(options.keys()).index(current_label), key=f"{key_prefix}_dep_{job['id']}",
    )
    if st.button("Save dependency", key=f"{key_prefix}_savedep_{job['id']}"):
        try:
            models.set_blocked_by(job["id"], options[choice])
        except models.JobRuleError as e:
            st.error(str(e))
        else:
            st.toast("Saved.", icon="✅")
            st.rerun()


def _apply_status(job_pk: int, new_status: str) -> None:
    try:
        models.set_status(job_pk, new_status)
    except models.JobRuleError as e:
        st.error(str(e))
    else:
        st.rerun()
