"""The client/company detail page — a client's name, wherever it's
referenced across jobs, invoices and the register, converges here. Since
every one of those reads client.name live via client_id (no denormalised
copy anywhere), renaming a company here updates it everywhere at once.
Name-editing is available to EC/admin/manager/super_admin, with a change
logged (who, when, old -> new); anyone who can reach this page (via a
clickable client name) sees the basic contact info and jobs on file."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import models
from core import ui
from core.constants import (
    RISK_COLORS,
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_PRINCIPAL,
    ROLE_SUPER_ADMIN,
    titlecase_name,
)

_STATUS_RISK = {
    models.COMPLIANCE_EXPIRED: "red",
    models.COMPLIANCE_URGENT: "amber",
    models.COMPLIANCE_UPCOMING: "grey",
    models.COMPLIANCE_OK: "green",
}


def render(user: dict, client_pk: int) -> None:
    ui.back_button("← Back")

    client = models.get_client(client_pk)
    if client is None:
        st.error("That client no longer exists.")
        return

    ui.page_header(titlecase_name(client["name"]), "Company details and jobs on file.")

    _edit_name_control(client, user)
    st.divider()
    _contacts_section(client, user)
    st.divider()
    _jobs_for_client(client)
    if models.staff_sees_compliance(user):
        st.divider()
        _compliance_section(client, user)


def _edit_name_control(client: dict, user: dict) -> None:
    if user["role"] not in (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        return
    with st.expander("Edit company name"):
        st.caption("Updates everywhere this client is shown — jobs, invoices, the register.")
        new_name = st.text_input("Company name", value=client["name"], key=f"clientname_{client['id']}")
        if st.button("Save name", key=f"saveclientname_{client['id']}", type="primary"):
            try:
                models.update_client_name(client["id"], new_name, actor_id=user["id"])
            except models.FieldEditError as e:
                st.error(str(e))
            else:
                st.toast("Company name updated.", icon="✅")
                st.rerun()

        edits = models.list_field_edits("client", client["id"])
        if edits:
            st.caption("Edit history:")
            for e in edits:
                st.caption(
                    f"{e['old_value']} → {e['new_value']} — {titlecase_name(e['changed_by_name']) or '—'}, "
                    f"{e['changed_at'].strftime('%d %b %Y, %H:%M')}"
                )


_CAN_EDIT_CONTACTS = (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN)


def _contacts_section(client: dict, user: dict) -> None:
    st.markdown("#### Contacts")
    st.write(f"**RC number:** {client.get('rc_number') or '—'}")
    st.write(f"**Status:** {'Active' if client['status'] == 'active' else 'Inactive'}")

    can_edit = user["role"] in _CAN_EDIT_CONTACTS
    contacts = models.list_client_contacts(client["id"])

    if not contacts:
        st.caption("No contacts on file yet.")
    for contact in contacts:
        if not can_edit:
            c1, c2, c3 = st.columns(3)
            c1.write(f"**Name:** {titlecase_name(contact['name']) or '—'}")
            c2.write(f"**Email:** {contact['email'] or '—'}")
            c3.write(f"**Phone:** {contact['phone'] or '—'}")
            continue
        c1, c2, c3, c4 = st.columns([2, 2, 1.6, 0.9])
        name = c1.text_input("Name", value=contact["name"] or "", key=f"contact_name_{contact['id']}")
        email = c2.text_input("Email", value=contact["email"] or "", key=f"contact_email_{contact['id']}")
        phone = c3.text_input("Phone", value=contact["phone"] or "", key=f"contact_phone_{contact['id']}")
        if c4.button("Save", key=f"contact_save_{contact['id']}"):
            models.update_client_contact(contact["id"], name, email, phone)
            st.toast("Contact updated.", icon="✅")
            st.rerun()

    if can_edit:
        with st.expander("+ Add contact"):
            c1, c2, c3, c4 = st.columns([2, 2, 1.6, 0.9])
            new_name = c1.text_input("Name", key=f"newcontact_name_{client['id']}")
            new_email = c2.text_input("Email", key=f"newcontact_email_{client['id']}")
            new_phone = c3.text_input("Phone", key=f"newcontact_phone_{client['id']}")
            if c4.button("Add", key=f"newcontact_add_{client['id']}", type="primary"):
                if not (new_name or new_email or new_phone):
                    st.error("Enter at least one field.")
                else:
                    models.add_client_contact(client["id"], new_name, new_email, new_phone)
                    st.toast("Contact added.", icon="✅")
                    st.rerun()


def _jobs_for_client(client: dict) -> None:
    st.markdown("#### Jobs")
    jobs = models.list_jobs(client_id=client["id"])
    ui.jobs_row_table(jobs, key_prefix=f"clientjobs_{client['id']}")


_COMPLIANCE_WIDTHS = [0.4, 2.0, 1.6, 2.0, 1.3, 1.3, 1.1]


def _compliance_section(client: dict, user: dict) -> None:
    st.markdown("#### Compliance items")
    items = models.list_compliance_items(client_id=client["id"])
    if not items:
        st.caption("No compliance items tracked for this client yet.")
    else:
        header = st.columns(_COMPLIANCE_WIDTHS)
        for col, label in zip(header, ["", "Document", "Position", "For", "Issue date", "Expiry date", "Status"]):
            col.markdown(f"**{label}**")
        for item in items:
            status = models.compliance_item_status(item["expiry_date"])
            color = RISK_COLORS[_STATUS_RISK[status]]
            cols = st.columns(_COMPLIANCE_WIDTHS)
            cols[0].markdown(f'<span class="rc-dot" style="background:{color}"></span>', unsafe_allow_html=True)
            cols[1].write(item["document_type"])
            cols[2].write(item.get("position") or "—")
            cols[3].write(titlecase_name(item.get("subject_name")) or "—")
            cols[4].write(item["issue_date"].isoformat() if item["issue_date"] else "—")
            cols[5].write(item["expiry_date"].isoformat() if item["expiry_date"] else "—")
            cols[6].write(models.COMPLIANCE_STATUS_LABELS[status])

    with st.expander("Add compliance item"):
        document_type = st.text_input("Document type *", key=f"comp_doctype_{client['id']}")
        position = st.text_input("Position (optional)", key=f"comp_position_{client['id']}")
        subject_name = st.text_input("Name (person, if applicable)", key=f"comp_subject_{client['id']}")
        c1, c2 = st.columns(2)
        with c1:
            issue_date = st.date_input("Issue date", value=None, key=f"comp_issue_{client['id']}")
        with c2:
            expiry_date = st.date_input("Expiry date", value=None, key=f"comp_expiry_{client['id']}")
        if st.button("Add item", key=f"comp_add_{client['id']}", type="primary"):
            if not document_type.strip():
                st.error("Document type is required.")
            else:
                models.create_compliance_item(
                    client["id"],
                    document_type,
                    position=position,
                    subject_name=subject_name,
                    issue_date=issue_date,
                    expiry_date=expiry_date,
                    created_by=user["id"],
                )
                st.toast("Compliance item added.", icon="✅")
                st.rerun()
