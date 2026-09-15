"""Front-office capture: log a request in seconds, or dismiss it with a
reason. This is the one door — nothing is worked until it exists here."""

from __future__ import annotations

import streamlit as st

from core import cit, models
from core import ui
from core.constants import SOURCE_CLIENT_EMAIL, SOURCE_LABELS, SOURCE_TEAM_GROUP_FORWARD, STATUS_LABELS_SHORT, humanize
from core.seed_data import PILLAR_TO_CATEGORY

MODE_JOB = "Log a job"
MODE_DISMISS = "Dismiss — not a job"
NEW_CLIENT_OPTION = "+ Add new client…"


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

    client_map = {c["name"]: c for c in clients}
    service_map = {s["name"]: s for s in services}

    col1, col2 = st.columns(2)
    with col1:
        client_name = st.selectbox(
            "Client *", options=[NEW_CLIENT_OPTION] + list(client_map.keys()), index=None,
            placeholder="Select client…", key="cap_client",
        )
    with col2:
        service_name = st.selectbox(
            "Service *", options=list(service_map.keys()), index=None, placeholder="Select service…", key="cap_service"
        )

    new_client_name = new_contact_name = new_contact_email = new_contact_phone = ""
    if client_name == NEW_CLIENT_OPTION:
        with st.container(border=True):
            st.caption("New client — logged straight into the client list, no need to leave this screen.")
            nc1, nc2 = st.columns(2)
            new_client_name = nc1.text_input("Client name *", key="cap_newclient_name")
            new_contact_name = nc2.text_input("Contact name", key="cap_newclient_contact")
            nc3, nc4 = st.columns(2)
            new_contact_email = nc3.text_input("Contact email", key="cap_newclient_email")
            new_contact_phone = nc4.text_input("Contact phone", key="cap_newclient_phone")

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

    # Soft routing nudge: once a service picks a category, staff assigned to
    # that module (core.models.module_specialist) float to the top and the
    # first one is pre-selected — anyone can still be chosen, this never
    # filters the list, so it can't cost a specialist a fast capture.
    ordered_staff = staff
    default_owner_index = None
    if service_name:
        category_for_owner = PILLAR_TO_CATEGORY[service_map[service_name]["pillar"]]
        assigned_ids = {a["staff_id"] for a in models.list_module_specialists(category_for_owner)}
        if assigned_ids:
            preferred = [s for s in staff if s["id"] in assigned_ids]
            rest = [s for s in staff if s["id"] not in assigned_ids]
            ordered_staff = preferred + rest
            default_owner_index = 0
    staff_map = {ui.staff_label(s): s for s in ordered_staff}

    col3, col4 = st.columns(2)
    with col3:
        owner_name = st.selectbox(
            "Owner *", options=list(staff_map.keys()), index=default_owner_index,
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

    # A brand-new client can't already have a job on file, so duplicate
    # detection only applies once an existing client + service are picked.
    duplicate = None
    if client_name and client_name != NEW_CLIENT_OPTION and service_name:
        duplicate = models.find_potential_duplicate(client_map[client_name]["id"], service_map[service_name]["code"])

    log_anyway = True
    if duplicate:
        st.warning(
            f"⚠️ This job may already exist — {ui.short_job_id(duplicate['job_id'])} "
            f"({STATUS_LABELS_SHORT.get(duplicate['status'], humanize(duplicate['status']))}), "
            f"owned by {duplicate['owner_name'] or '—'}, logged {duplicate['created_at'].strftime('%d %b %Y')}."
        )
        log_anyway = st.checkbox("Log anyway — this is a separate, genuine request", key="cap_loganyway")

    st.write("")
    if st.button("Log job", type="primary", key="cap_submit"):
        if not client_name or not service_name or not owner_name:
            st.error("Client, service and owner are required.")
            return
        if duplicate and not log_anyway:
            st.error("Confirm this isn't a duplicate — check 'Log anyway' to continue.")
            return

        if client_name == NEW_CLIENT_OPTION:
            if not new_client_name.strip():
                st.error("Enter a name for the new client.")
                return
            client = models.create_client(
                new_client_name.strip(),
                contact_name=new_contact_name.strip() or None,
                contact_email=new_contact_email.strip() or None,
                contact_phone=new_contact_phone.strip() or None,
            )
        else:
            client = client_map[client_name]

        service = service_map[service_name]
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

        if category == "cit":
            # A brand-new CIT job (another audit, an unfiled return...) can
            # be exactly what should now block an existing TCC for this
            # client — re-check right away rather than waiting for the
            # periodic sweep.
            cit.sync_tcc_gate(actor_id=user["id"])

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
