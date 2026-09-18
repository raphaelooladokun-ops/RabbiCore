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

from core import models
from core import ui
from core.constants import RISK_AMBER, RISK_COLORS, RISK_EMOJI, RISK_GREEN, RISK_GREY, RISK_RED, titlecase_name

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
