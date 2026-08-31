"""Front-office capture: log a request in seconds, or dismiss it with a
reason. This is the one door — nothing is worked until it exists here."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import SOURCE_CLIENT_EMAIL, SOURCE_LABELS, SOURCE_TEAM_GROUP_FORWARD
from core.seed_data import PILLAR_TO_CATEGORY

MODE_JOB = "Log a job"
MODE_DISMISS = "Dismiss — not a job"


def render(user: dict) -> None:
    ui.page_header("Capture", "Nothing is worked until it's logged here. Keep it fast.")

    mode = st.radio("", [MODE_JOB, MODE_DISMISS], horizontal=True, label_visibility="collapsed")
    st.write("")

    if mode == MODE_JOB:
        _job_form(user)
    else:
        _dismiss_form(user)


def _source_radio(key: str) -> str:
    choice = st.radio(
        "Source", list(SOURCE_LABELS.values()), horizontal=True, key=key,
    )
    for code, label in SOURCE_LABELS.items():
        if label == choice:
            return code
    return SOURCE_CLIENT_EMAIL


def _job_form(user: dict) -> None:
    clients = models.list_clients(active_only=True)
    services = models.list_service_catalogue()
    staff = [s for s in models.list_staff(active_only=True) if s["role"] != "client"]

    if not clients:
        st.warning("No active clients yet. Add a client before logging a job.")
        return

    client_map = {c["name"]: c for c in clients}
    service_map = {s["name"]: s for s in services}
    staff_map = {s["name"]: s for s in staff}

    col1, col2 = st.columns(2)
    with col1:
        client_name = st.selectbox(
            "Client *", options=list(client_map.keys()), index=None, placeholder="Select client…", key="cap_client"
        )
    with col2:
        service_name = st.selectbox(
            "Service *", options=list(service_map.keys()), index=None, placeholder="Select service…", key="cap_service"
        )

    attributes: dict = {}
    if service_name:
        service = service_map[service_name]
        fields = service["fields"] or []
        if fields:
            st.caption(f"{service['pillar']} details")
            field_cols = st.columns(min(len(fields), 3))
            for i, f in enumerate(fields):
                target = field_cols[i % len(field_cols)]
                val = target.selectbox(
                    f["label"], options=f["options"], index=None,
                    placeholder="Select…", key=f"cap_attr_{f['key']}",
                )
                if val:
                    attributes[f["key"]] = val

    col3, col4 = st.columns(2)
    with col3:
        owner_name = st.selectbox(
            "Owner *", options=list(staff_map.keys()), index=None,
            placeholder="Who owns this?", key="cap_owner",
        )
    with col4:
        source = _source_radio("cap_source")

    description = st.text_input(
        "Brief description", placeholder="e.g. Quota renewal for 2 slots", key="cap_description"
    )

    with st.expander("More detail (optional)"):
        sla_date = st.date_input("SLA / due date", value=None, key="cap_sla")
        waiting_on_client = st.text_input(
            "Waiting on client for… (optional)", key="cap_waiting",
            placeholder="e.g. Scanned copy of old CERPAC card",
        )

    st.write("")
    if st.button("Log job", type="primary", key="cap_submit"):
        if not client_name or not service_name or not owner_name:
            st.error("Client, service and owner are required.")
            return

        service = service_map[service_name]
        client = client_map[client_name]
        owner = staff_map[owner_name]
        category = PILLAR_TO_CATEGORY[service["pillar"]]
        title = description.strip() if description else service["name"]

        job = models.create_job(
            client_id=client["id"],
            category=category,
            service_type=service["code"],
            title=title,
            description=description.strip() or None,
            owner_id=owner["id"],
            source=source,
            created_by=user["id"],
            sla_date=sla_date,
            attributes=attributes,
            waiting_on_client=waiting_on_client.strip() or None,
        )

        st.success(f"Logged — **{job['job_id']}**, owner {owner_name}.")
        st.code(f"Logged, {job['job_id']}, owner {owner_name}", language=None)
        st.caption("Copy the line above back into the thread as confirmation.")


def _dismiss_form(user: dict) -> None:
    clients = models.list_clients(active_only=True)
    client_map = {c["name"]: c for c in clients}
    no_client = "— not linked to a client —"

    client_name = st.selectbox(
        "Client (optional)", options=[no_client] + list(client_map.keys()), key="dis_client"
    )
    description = st.text_area(
        "What was this about?", placeholder="Short summary of the message or request", key="dis_description"
    )
    reason = st.text_input(
        "Reason for dismissing *", placeholder="e.g. Just a question, no action needed", key="dis_reason"
    )
    source = _source_radio("dis_source")

    st.write("")
    if st.button("Log dismissal", key="dis_submit"):
        if not description.strip() or not reason.strip():
            st.error("Description and reason are required.")
            return
        client_id = client_map[client_name]["id"] if client_name != no_client else None
        job = models.create_dismissed_job(
            client_id=client_id,
            title=description.strip()[:120],
            description=description.strip(),
            source=source,
            created_by=user["id"],
            dismissed_reason=reason.strip(),
        )
        st.success(f"Logged as dismissed — **{job['job_id']}**.")
