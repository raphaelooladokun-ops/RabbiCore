"""The invoice document — laid out like a real invoice (bill-to, line
items, total), not a form. Principal can edit amounts/descriptions and
approve, or reject with a reason; admin can revise a rejected invoice, mark
it sent to the client, or mark it paid. A downloadable PDF is available once
it's approved."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import models
from core import pdf as pdf_module
from core import ui
from core.constants import (
    INVOICE_STATUS_LABELS,
    ROLE_ADMIN,
    ROLE_PRINCIPAL,
    ROLE_SPECIALIST,
    ROLE_SUPER_ADMIN,
    humanize,
)


def render(user: dict, invoice_pk: int) -> None:
    ui.back_button("← Back")

    invoice = models.get_invoice(invoice_pk)
    if invoice is None:
        st.error("Invoice not found.")
        return

    if user["role"] == ROLE_SUPER_ADMIN:
        pass  # full access — every invoice, every action below
    elif user["role"] == ROLE_SPECIALIST:
        # View-only: only if they own at least one job on this invoice.
        invoice_jobs = models.list_jobs_for_invoice(invoice["id"])
        if not any(j["owner_id"] == user["id"] for j in invoice_jobs):
            st.error("You don't have access to this invoice.")
            return
    elif user["role"] not in (ROLE_ADMIN, ROLE_PRINCIPAL):
        st.error("You don't have access to this invoice.")
        return

    lines = models.list_invoice_lines(invoice["id"])
    total = sum(float(line["amount"]) for line in lines)

    _document_header(invoice, total)

    if invoice["status"] == "rejected":
        st.warning(f"**Rejected by {invoice['rejected_by_name'] or '—'}:** {invoice['rejection_reason']}")

    editable_by_principal = (
        user["role"] in (ROLE_PRINCIPAL, ROLE_SUPER_ADMIN) and invoice["status"] == "pending_approval"
    )

    if editable_by_principal:
        _principal_review(user, invoice, lines)
    else:
        _read_only_lines(lines, total)

    _lifecycle_actions(user, invoice, lines, total)
    _edit_invoice_code_control(invoice, user)


def _lifecycle_actions(user: dict, invoice: dict, lines: list, total: float) -> None:
    if user["role"] in (ROLE_ADMIN, ROLE_SUPER_ADMIN) and invoice["status"] == "rejected":
        st.write("")
        if st.button("Revise & resubmit", type="primary", key="inv_revise"):
            st.session_state["invoice_revise_id"] = invoice["id"]
            ui.go_to_create_invoice()

    if invoice["status"] in ("approved", "paid") and user["role"] in (ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        st.write("")
        pdf_bytes = pdf_module.build_invoice_pdf(invoice, lines, total)
        st.download_button(
            "Download PDF", data=pdf_bytes, file_name=f"{invoice['invoice_code']}.pdf",
            mime="application/pdf", key="inv_pdf",
        )

    if user["role"] in (ROLE_ADMIN, ROLE_SUPER_ADMIN) and invoice["status"] in ("approved", "paid"):
        st.write("")
        if invoice.get("sent_at"):
            st.caption(f"Sent to client on {invoice['sent_at'].strftime('%d %b %Y')}.")
        else:
            if st.button("Mark sent to client", key="inv_marksent"):
                models.mark_invoice_sent(invoice["id"], user["id"])
                st.toast("Marked sent to client.", icon="✅")
                st.rerun()

    if user["role"] in (ROLE_ADMIN, ROLE_SUPER_ADMIN) and invoice["status"] == "approved":
        st.write("")
        with st.expander("Mark as paid"):
            with st.form(key="inv_paid_form"):
                reference = st.text_input("Payment reference *", key="inv_payref")
                payment_date = st.date_input("Payment date *", value=date.today(), key="inv_paydate")
                if st.form_submit_button("Mark paid"):
                    try:
                        models.mark_invoice_paid(invoice["id"], reference, payment_date)
                    except models.InvoicePaymentError as e:
                        st.error(str(e))
                    else:
                        st.toast("Marked paid.", icon="✅")
                        st.rerun()

    if invoice["status"] == "paid":
        paid_on = invoice["payment_date"].isoformat() if invoice.get("payment_date") else "—"
        st.caption(f"Paid — ref {invoice.get('payment_reference') or '—'}, {paid_on}.")


def _edit_invoice_code_control(invoice: dict, user: dict) -> None:
    """EC (principal)/admin/super_admin only: correct or manually set this
    invoice's number. invoice_code is normally auto-generated and never
    touched again — this exists for the rare "this was set wrong" case,
    and every change is logged (who, when, old -> new) so a correction is
    never silent."""
    if user["role"] not in (ROLE_ADMIN, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN):
        return
    st.write("")
    with st.expander("Edit invoice number"):
        new_code = st.text_input("Invoice code", value=invoice["invoice_code"], key="inv_editcode")
        if st.button("Save invoice number", key="inv_savecode"):
            try:
                models.update_invoice_code(invoice["id"], new_code, actor_id=user["id"])
            except models.CodeEditError as e:
                st.error(str(e))
            else:
                st.toast("Invoice number updated.", icon="✅")
                st.rerun()

        edits = models.list_code_edits("invoice", invoice["id"])
        if edits:
            st.caption("Edit history:")
            for e in edits:
                st.caption(
                    f"{e['old_code']} → {e['new_code']} — {e['changed_by_name'] or '—'}, "
                    f"{e['changed_at'].strftime('%d %b %Y, %H:%M')}"
                )


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
        c2.caption(ui.short_job_id(line["job_code"]))
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
            c2.caption(ui.short_job_id(line["job_code"]))
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
