"""The invoice document — laid out like a real invoice (bill-to, line
items, total), not a form. Principal can edit amounts/descriptions and
approve, or reject with a reason; admin can revise a rejected invoice or
mark an approved one paid."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import INVOICE_STATUS_LABELS, ROLE_ADMIN, ROLE_PRINCIPAL, humanize


def render(user: dict, invoice_pk: int) -> None:
    ui.back_button("← Back")

    invoice = models.get_invoice(invoice_pk)
    if invoice is None:
        st.error("Invoice not found.")
        return
    if user["role"] not in (ROLE_ADMIN, ROLE_PRINCIPAL):
        st.error("You don't have access to this invoice.")
        return

    lines = models.list_invoice_lines(invoice["id"])
    total = sum(float(line["amount"]) for line in lines)

    _document_header(invoice, total)

    if invoice["status"] == "rejected":
        st.warning(f"**Rejected by {invoice['rejected_by_name'] or '—'}:** {invoice['rejection_reason']}")

    editable_by_principal = user["role"] == ROLE_PRINCIPAL and invoice["status"] == "pending_approval"

    if editable_by_principal:
        _principal_review(user, invoice, lines)
    else:
        _read_only_lines(lines, total)

    if user["role"] == ROLE_ADMIN and invoice["status"] == "rejected":
        st.write("")
        if st.button("Revise & resubmit", type="primary", key="inv_revise"):
            st.session_state["invoice_revise_id"] = invoice["id"]
            ui.go_to_create_invoice()

    if user["role"] == ROLE_ADMIN and invoice["status"] == "approved":
        st.write("")
        if st.button("Mark paid", key="inv_markpaid"):
            models.set_invoice_status(invoice["id"], "paid")
            st.toast("Marked paid.", icon="✅")
            st.rerun()


def _document_header(invoice: dict, total: float) -> None:
    status_label = humanize(invoice["status"], INVOICE_STATUS_LABELS)
    st.markdown(f'<div class="rc-page-title">{invoice["invoice_code"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<span class="rc-badge rc-badge-grey">{status_label}</span>', unsafe_allow_html=True)
    st.write("")

    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Bill to**")
            st.write(invoice["client_name"])
            if invoice["client_contact_name"]:
                st.caption(invoice["client_contact_name"])
            if invoice["client_contact_email"]:
                st.caption(invoice["client_contact_email"])
        with c2:
            st.markdown("**Invoice details**")
            st.write(f"Date: {invoice['invoice_date'].isoformat()}")
            st.write(f"Submitted by: {invoice['created_by_name'] or '—'}")
            if invoice["approved_by_name"]:
                st.write(f"Approved by: {invoice['approved_by_name']}")
        st.markdown(f"### Total: ₦{total:,.2f}")
    st.write("")


def _read_only_lines(lines: list, total: float) -> None:
    st.markdown("#### Line items")
    if not lines:
        st.caption("No line items.")
        return
    h1, h2, h3 = st.columns([3.5, 2, 1.5])
    h1.markdown("**Description**")
    h2.markdown("**Job**")
    h3.markdown("**Amount**")
    for line in lines:
        c1, c2, c3 = st.columns([3.5, 2, 1.5])
        c1.write(line["description"])
        c2.caption(line["job_code"])
        c3.write(f"₦{float(line['amount']):,.2f}")


def _principal_review(user: dict, invoice: dict, lines: list) -> None:
    st.markdown("#### Line items")
    st.caption(
        "You can edit descriptions and amounts. Which jobs are on this invoice is admin-only — "
        "if the composition is wrong, reject it back to admin instead."
    )

    with st.form(key=f"review_form_{invoice['id']}"):
        h1, h2, h3 = st.columns([3.5, 2, 1.5])
        h1.markdown("**Description**")
        h2.markdown("**Job**")
        h3.markdown("**Amount**")
        edited = []
        running_total = 0.0
        for line in lines:
            c1, c2, c3 = st.columns([3.5, 2, 1.5])
            desc = c1.text_input(
                "Description", value=line["description"], key=f"rev_desc_{line['id']}",
                label_visibility="collapsed",
            )
            c2.caption(line["job_code"])
            amount = c3.number_input(
                "Amount", value=float(line["amount"]), min_value=0.0, step=500.0, format="%.2f",
                key=f"rev_amt_{line['id']}", label_visibility="collapsed",
            )
            running_total += amount
            edited.append({"id": line["id"], "description": desc, "amount": amount})
        st.markdown(f"**Total: ₦{running_total:,.2f}**")
        approve_clicked = st.form_submit_button("Approve", type="primary")

    if approve_clicked:
        models.update_invoice_lines(invoice["id"], edited)
        models.approve_invoice(invoice["id"], user["id"])
        st.toast(f"{invoice['invoice_code']} approved.", icon="✅")
        st.rerun()

    st.write("")
    with st.expander("Reject this invoice"):
        with st.form(key=f"reject_form_{invoice['id']}"):
            reason = st.text_area("Reason *", placeholder="e.g. Missing the STATE-ITF job for this client")
            reject_clicked = st.form_submit_button("Reject back to admin")
        if reject_clicked:
            if not reason.strip():
                st.error("A reason is required to reject an invoice.")
            else:
                models.reject_invoice(invoice["id"], user["id"], reason.strip())
                st.toast("Invoice rejected — sent back to admin.", icon="↩️")
                st.rerun()
