"""Compliance Tracker: per-client compliance items (CERPAC cards, quota
approvals, tax clearance certificates, ...) so nothing lapses silently.
Visible to EC, manager, admin, super_admin, and immigration specialists
assigned to that module (app.py decides who reaches this page — see
models.staff_sees_compliance). Urgency is front-and-centre: a summary
across every client comes first, the client-card grid below is for
browsing into detail, not where urgency hides."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import compliance_bulk_import, models
from core import ui
from core.constants import (
    RISK_AMBER,
    RISK_COLORS,
    RISK_EMOJI,
    RISK_GREEN,
    RISK_GREY,
    RISK_RED,
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_PRINCIPAL,
    ROLE_SUPER_ADMIN,
    titlecase_name,
)

_CAN_BULK_UPLOAD = (ROLE_ADMIN, ROLE_MANAGER, ROLE_PRINCIPAL, ROLE_SUPER_ADMIN)
_PREVIEW_KEY = "compliance_bulk_preview"
_FILENAME_KEY = "_compliance_bulk_filename"
_RESULT_KEY = "compliance_bulk_result"
_EPOCH_KEY = "_compliance_bulk_epoch"

_STATUS_COLOR = {
    models.COMPLIANCE_EXPIRED: RISK_RED,
    models.COMPLIANCE_URGENT: RISK_AMBER,
    models.COMPLIANCE_UPCOMING: RISK_GREY,
    models.COMPLIANCE_OK: RISK_GREEN,
}
_STATUS_ORDER = [
    models.COMPLIANCE_EXPIRED, models.COMPLIANCE_URGENT, models.COMPLIANCE_UPCOMING, models.COMPLIANCE_OK,
]
_CARDS_PER_ROW = 4


def _compliance_legend() -> None:
    items = "".join(
        f'<span class="rc-legend-item">'
        f'<span class="rc-dot" style="background:{RISK_COLORS[_STATUS_COLOR[status]]}"></span>'
        f'{models.COMPLIANCE_STATUS_LABELS[status]}</span>'
        for status in _STATUS_ORDER
    )
    st.markdown(f'<div class="rc-legend">{items}</div>', unsafe_allow_html=True)


def render(user: dict) -> None:
    ui.page_header("Compliance Tracker", "Expiring compliance items across every client — nothing lapses silently.")
    _compliance_legend()

    summary = models.compliance_summary()
    _urgent_summary(summary)
    st.divider()
    _client_cards(summary)

    if user["role"] in _CAN_BULK_UPLOAD:
        st.divider()
        _bulk_upload_section(user)


def _item_subject_label(item: dict) -> str:
    parts = [p for p in (item.get("position"), titlecase_name(item.get("subject_name")) or None) if p]
    return " — ".join(parts) if parts else "—"


_SUMMARY_WIDTHS = [0.4, 2.0, 2.0, 1.8, 1.2, 1.1]


def _urgent_summary(summary: dict) -> None:
    st.markdown("#### Needs attention — across all clients")
    totals = summary["totals"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Expired", totals.get(models.COMPLIANCE_EXPIRED, 0))
    c2.metric("Urgent", totals.get(models.COMPLIANCE_URGENT, 0))
    c3.metric("Needs work to start", totals.get(models.COMPLIANCE_UPCOMING, 0))

    flagged = [
        item for items in summary["by_client"].values() for item in items
        if item["status"] != models.COMPLIANCE_OK
    ]
    if not flagged:
        st.caption("Nothing expired, urgent, or needing renewal work started right now.")
        return

    flagged.sort(key=lambda it: (_STATUS_ORDER.index(it["status"]), it["expiry_date"] or date.max))

    st.write("")
    header = st.columns(_SUMMARY_WIDTHS)
    for col, label in zip(header, ["", "Client", "Document", "For", "Expiry", "Status"]):
        col.markdown(f"**{label}**")

    for item in flagged:
        cols = st.columns(_SUMMARY_WIDTHS)
        cols[0].write(RISK_EMOJI[_STATUS_COLOR[item["status"]]])
        if cols[1].button(
            titlecase_name(item["client_name"]), key=f"comp_flag_client_{item['id']}", type="tertiary",
        ):
            ui.go_to_client(item["client_id"])
        cols[2].write(item["document_type"])
        cols[3].write(_item_subject_label(item))
        cols[4].write(item["expiry_date"].isoformat() if item["expiry_date"] else "—")
        cols[5].write(models.COMPLIANCE_STATUS_LABELS[item["status"]])


def _worst_status(items: list) -> str:
    for status in _STATUS_ORDER:
        if any(it["status"] == status for it in items):
            return status
    return models.COMPLIANCE_OK


def _client_cards(summary: dict) -> None:
    st.markdown("#### Clients")
    st.caption("Click a client to see all their compliance items, with issue and expiry dates.")
    clients = models.list_clients(active_only=True)
    if not clients:
        st.caption("No clients on file yet.")
        return

    by_client = summary["by_client"]
    for row_start in range(0, len(clients), _CARDS_PER_ROW):
        row = clients[row_start:row_start + _CARDS_PER_ROW]
        cols = st.columns(_CARDS_PER_ROW)
        for col, client in zip(cols, row):
            items = by_client.get(client["id"], [])
            worst = _worst_status(items)
            color = RISK_COLORS[_STATUS_COLOR[worst]]
            with col:
                with st.container(border=True):
                    st.markdown(
                        f'<span class="rc-dot" style="background:{color}"></span> '
                        f'**{titlecase_name(client["name"])}**',
                        unsafe_allow_html=True,
                    )
                    st.caption(f"{len(items)} item(s) tracked" if items else "No items tracked")
                    if st.button("Open", key=f"comp_card_{client['id']}", use_container_width=True):
                        ui.go_to_client(client["id"])


def _bulk_upload_section(user: dict) -> None:
    st.markdown("#### Bulk upload compliance items")
    st.caption(
        "Expected columns: **Client, Document Type, Position, Name, Issue Date, Expiry Date** "
        "(dates as DD/MM/YYYY). Client must match an existing record exactly — new clients aren't "
        "created from here. Only the item and its dates are stored; no files or ID numbers."
    )

    result = st.session_state.pop(_RESULT_KEY, None)
    if result:
        msg = f"Created {result['items_created']} compliance item(s)."
        if result["items_skipped"]:
            msg += f" Skipped {result['items_skipped']} row(s) missing a client or document type."
        st.success(msg)

    epoch = st.session_state.get(_EPOCH_KEY, 0)
    uploaded = st.file_uploader("CSV file", type=["csv"], key=f"compliance_bulk_file_{epoch}")
    if uploaded is not None and st.session_state.get(_FILENAME_KEY) != uploaded.name:
        _load_compliance_preview(uploaded)

    preview = st.session_state.get(_PREVIEW_KEY)
    if preview:
        _render_compliance_preview(preview, user)


def _load_compliance_preview(uploaded) -> None:
    try:
        rows = compliance_bulk_import.parse_csv(uploaded.getvalue())
    except compliance_bulk_import.ComplianceBulkImportError as e:
        st.error(str(e))
        st.session_state.pop(_PREVIEW_KEY, None)
        st.session_state.pop(_FILENAME_KEY, None)
        return

    if not rows:
        st.warning("No data rows found in that file.")
        st.session_state.pop(_PREVIEW_KEY, None)
        st.session_state.pop(_FILENAME_KEY, None)
        return

    clients = models.list_clients()
    st.session_state[_PREVIEW_KEY] = compliance_bulk_import.build_preview(rows, clients)
    st.session_state[_FILENAME_KEY] = uploaded.name


def _render_compliance_preview(preview: list, user: dict) -> None:
    clients = models.list_clients()
    client_options = {titlecase_name(c["name"]): c for c in clients}

    unmatched = [r for r in preview if not r["client"]]
    bad_dates = [r for r in preview if r["issue_date_error"] or r["expiry_date_error"]]
    missing_doctype = [r for r in preview if r["missing_doctype"]]

    st.write("")
    st.markdown("#### Preview")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows", len(preview))
    m2.metric("Unmatched clients", len(unmatched))
    m3.metric("Unparseable dates", len(bad_dates))
    m4.metric("Missing document type", len(missing_doctype))

    if unmatched or bad_dates or missing_doctype:
        st.warning("Rows marked ⚠️ below need a fix before commit — pick a client manually, correct a date, or add a document type.")

    for r in preview:
        raw = r["raw"]
        with st.container(border=True):
            c1, c2 = st.columns([2, 3])
            with c1:
                if r["client"]:
                    st.write(f"**{titlecase_name(raw[compliance_bulk_import.COL_CLIENT])}**  ✅ matched")
                    r["_final_client"] = r["client"]
                else:
                    st.write(f"**{raw[compliance_bulk_import.COL_CLIENT] or '— no client —'}**  ⚠️ no match")
                    choice = st.selectbox(
                        "Client (pick manually)", options=list(client_options.keys()), index=None,
                        placeholder="Select…", key=f"compbulk_client_{r['index']}",
                    )
                    r["_final_client"] = client_options.get(choice)
                doctype_label = "Document type" + (" ⚠️ required" if r["missing_doctype"] else "")
                st.caption(f"{doctype_label}: {raw[compliance_bulk_import.COL_DOCTYPE] or '—'}")
                st.caption(
                    f"Position: {raw[compliance_bulk_import.COL_POSITION] or '—'} · "
                    f"Name: {raw[compliance_bulk_import.COL_NAME] or '—'}"
                )
            with c2:
                issue_label = "Issue date" + (" ⚠️ couldn't parse — set manually" if r["issue_date_error"] else "")
                r["_final_issue_date"] = st.date_input(
                    issue_label, value=r["issue_date"], key=f"compbulk_issue_{r['index']}",
                )
                expiry_label = "Expiry date" + (" ⚠️ couldn't parse — set manually" if r["expiry_date_error"] else "")
                r["_final_expiry_date"] = st.date_input(
                    expiry_label, value=r["expiry_date"], key=f"compbulk_expiry_{r['index']}",
                )

    st.write("")
    if st.button(f"Commit import — create up to {len(preview)} item(s)", type="primary", key="compbulk_commit"):
        resolved = [
            {
                "client_id": r["_final_client"]["id"] if r.get("_final_client") else None,
                "document_type": r["raw"][compliance_bulk_import.COL_DOCTYPE],
                "position": r["raw"][compliance_bulk_import.COL_POSITION],
                "subject_name": r["raw"][compliance_bulk_import.COL_NAME],
                "issue_date": r.get("_final_issue_date"),
                "expiry_date": r.get("_final_expiry_date"),
            }
            for r in preview
        ]
        summary = compliance_bulk_import.commit_import(resolved, user["id"])
        st.session_state.pop(_PREVIEW_KEY, None)
        st.session_state.pop(_FILENAME_KEY, None)
        st.session_state[_RESULT_KEY] = summary
        st.session_state[_EPOCH_KEY] = st.session_state.get(_EPOCH_KEY, 0) + 1
        st.rerun()
