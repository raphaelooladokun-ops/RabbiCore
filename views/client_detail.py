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
    FORCE_DELETE_PIN,
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
    if user["role"] == ROLE_SUPER_ADMIN:
        st.divider()
        _merge_control(client, user)
        st.divider()
        _delete_control(client, user)


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


def _merge_control(client: dict, user: dict) -> None:
    with st.expander("⚠️ Merge duplicate (super admin)"):
        st.caption(
            "Folds this record into another client — every job, invoice, compliance item and contact "
            f"attributed to **{titlecase_name(client['name'])}** moves to the surviving record, then this "
            "one is deleted. Use this to clean up a duplicate created under a slightly different name."
        )
        others = [c for c in models.list_clients() if c["id"] != client["id"]]
        if not others:
            st.caption("No other clients to merge into.")
            return
        options = {f"{titlecase_name(c['name'])} (#{c['id']})": c for c in others}
        target_label = st.selectbox(
            "Surviving client", options=list(options.keys()), index=None,
            placeholder="Select the record to keep…", key=f"mergeinto_{client['id']}",
        )
        if not target_label:
            return
        target = options[target_label]
        confirm = st.checkbox(
            f"Yes, merge {titlecase_name(client['name'])} into {titlecase_name(target['name'])} — "
            "I understand this cannot be undone.",
            key=f"mergeconfirm_{client['id']}",
        )
        if st.button("Merge", key=f"mergebtn_{client['id']}", disabled=not confirm, type="primary"):
            try:
                models.merge_clients(client["id"], target["id"])
            except models.ClientMergeError as e:
                st.error(str(e))
            else:
                st.toast(f"Merged into {titlecase_name(target['name'])}.", icon="✅")
                ui.go_to_client(target["id"])
                st.rerun()


def _delete_control(client: dict, user: dict) -> None:
    with st.expander("🗑️ Delete client (super admin)"):
        summary = models.client_deletion_summary(client["id"])
        has_history = summary["jobs"] > 0 or summary["invoices"] > 0 or summary["compliance_items"] > 0

        if has_history:
            extra = (
                f", plus {summary['file_register_entries']} file register entry(ies)"
                if summary["file_register_entries"] else ""
            )
            st.warning(
                f"**{titlecase_name(client['name'])}** has **{summary['jobs']} job(s)**, "
                f"**{summary['invoices']} invoice(s)** and **{summary['compliance_items']} compliance "
                f"item(s)** attached{extra}. Deleting this client permanently deletes ALL of it too — "
                "every job, every invoice, every compliance item. This cannot be undone."
            )
        else:
            st.caption("No jobs, invoices or compliance items are attached to this client.")
        st.caption("This is a permanent action and cannot be undone. Requires the 4-digit PIN.")

        confirm_label = f"Yes, permanently delete {titlecase_name(client['name'])}"
        if has_history:
            confirm_label += " and everything attached to it"
        confirm_label += " — I understand this cannot be undone."
        confirm = st.checkbox(confirm_label, key=f"delclient_confirm_{client['id']}")
        pin = st.text_input(
            "4-digit PIN", type="password", max_chars=4, key=f"delclient_pin_{client['id']}",
        )
        if st.button(
            "Delete client permanently", key=f"delclient_btn_{client['id']}",
            disabled=not confirm, type="primary",
        ):
            if pin != FORCE_DELETE_PIN:
                st.error("Incorrect PIN.")
            else:
                try:
                    models.delete_client(client["id"], force=has_history)
                except models.ClientDeleteError as e:
                    st.error(str(e))
                else:
                    st.toast(f"{titlecase_name(client['name'])} permanently deleted.", icon="✅")
                    ui.clear_all_nav()
                    st.rerun()
