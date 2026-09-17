"""The client/company detail page — a client's name, wherever it's
referenced across jobs, invoices and the register, converges here. Since
every one of those reads client.name live via client_id (no denormalised
copy anywhere), renaming a company here updates it everywhere at once.
Name-editing is available to EC/admin/manager/super_admin, with a change
logged (who, when, old -> new); anyone who can reach this page (via a
clickable client name) sees the basic contact info and jobs on file."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN


def render(user: dict, client_pk: int) -> None:
    ui.back_button("← Back")

    client = models.get_client(client_pk)
    if client is None:
        st.error("That client no longer exists.")
        return

    ui.page_header(client["name"], "Company details and jobs on file.")

    _edit_name_control(client, user)
    st.divider()
    _contact_info(client)
    st.divider()
    _jobs_for_client(client)


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
                    f"{e['old_value']} → {e['new_value']} — {e['changed_by_name'] or '—'}, "
                    f"{e['changed_at'].strftime('%d %b %Y, %H:%M')}"
                )


def _contact_info(client: dict) -> None:
    st.markdown("#### Contact")
    c1, c2 = st.columns(2)
    with c1:
        st.write(f"**RC number:** {client.get('rc_number') or '—'}")
        st.write(f"**Contact name:** {client.get('contact_name') or '—'}")
    with c2:
        st.write(f"**Contact email:** {client.get('contact_email') or '—'}")
        st.write(f"**Contact phone:** {client.get('contact_phone') or '—'}")
    st.write(f"**Status:** {'Active' if client['status'] == 'active' else 'Inactive'}")


def _jobs_for_client(client: dict) -> None:
    st.markdown("#### Jobs")
    jobs = models.list_jobs(client_id=client["id"])
    ui.jobs_row_table(jobs, key_prefix=f"clientjobs_{client['id']}")
