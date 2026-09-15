"""Bulk job upload — super_admin only. Upload a CSV export of legacy/
external job records and create them all in one action. Always previews
first: counts, new clients, and any row whose service or owner didn't
match cleanly — nothing is ever silently imported. See core/bulk_import.py
for the column mapping and matching rules."""

from __future__ import annotations

import streamlit as st

from core import bulk_import, models
from core import ui

_PREVIEW_KEY = "bulk_upload_preview"
_FILENAME_KEY = "_bulk_upload_filename"
_RESULT_KEY = "bulk_upload_result"
_EPOCH_KEY = "_bulk_upload_epoch"


def render(user: dict) -> None:
    ui.page_header(
        "Bulk Job Upload",
        "Import a CSV of jobs — matched to existing clients, services and owners where possible.",
    )

    result = st.session_state.pop(_RESULT_KEY, None)
    if result:
        st.success(
            f"Imported {result['jobs_created']} job(s) as New, created "
            f"{result['clients_created']} new client(s). All appear in the Register now."
        )

    st.caption(
        "Expected columns: **Owner/Source, Status, Client/Company, Service/Task, Period/Details, Notes**. "
        "Every imported job is created as **New** — the Status column is kept as context in the job's "
        "internal notes, never applied as the job's actual status."
    )

    epoch = st.session_state.get(_EPOCH_KEY, 0)
    uploaded = st.file_uploader("CSV file", type=["csv"], key=f"bulk_upload_file_{epoch}")
    if uploaded is not None and st.session_state.get(_FILENAME_KEY) != uploaded.name:
        _load_preview(uploaded)

    preview = st.session_state.get(_PREVIEW_KEY)
    if preview:
        _render_preview(preview, user)


def _load_preview(uploaded) -> None:
    try:
        rows = bulk_import.parse_csv(uploaded.getvalue())
    except bulk_import.BulkImportError as e:
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
    services = models.list_service_catalogue()
    staff = [s for s in models.list_staff(active_only=True) if s["role"] != "client"]
    st.session_state[_PREVIEW_KEY] = bulk_import.build_preview(rows, clients, services, staff)
    st.session_state[_FILENAME_KEY] = uploaded.name


def _render_preview(preview: list, user: dict) -> None:
    services = models.list_service_catalogue()
    staff = [s for s in models.list_staff(active_only=True) if s["role"] != "client"]

    service_options = {"— no match, choose manually —": None}
    service_options.update({f"{s['name']} ({s['pillar']})": s["code"] for s in services})
    owner_options = {"— unassigned —": None}
    owner_options.update({ui.staff_label(s): s["id"] for s in staff})

    new_client_names = {
        r["raw"][bulk_import.COL_CLIENT] for r in preview if r["will_create_client"]
    }
    flagged_service = [r for r in preview if r["service_confidence"] != "exact"]
    flagged_owner = [r for r in preview if r["owner_confidence"] != "exact"]

    st.write("")
    st.markdown("#### Preview")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Jobs to create", len(preview))
    m2.metric("New clients", len(new_client_names))
    m3.metric("Unmatched services", len(flagged_service))
    m4.metric("Unmatched owners", len(flagged_owner))

    if flagged_service or flagged_owner:
        st.warning(
            "Rows marked ⚠️ below didn't match an existing service or owner cleanly — "
            "pick one manually, or leave as-is and fix it after import (unassigned owners "
            "can be bulk-assigned from the Register)."
        )

    for r in preview:
        raw = r["raw"]
        with st.container(border=True):
            c1, c2 = st.columns([2, 3])
            with c1:
                client_label = raw[bulk_import.COL_CLIENT] or "— no client —"
                if r["will_create_client"]:
                    st.write(f"**{client_label}**  🆕 new client")
                elif r["client"]:
                    st.write(f"**{client_label}**  ✅ matched")
                else:
                    st.write(f"**{client_label}**")
                st.caption(f"Owner/Source (raw): {raw[bulk_import.COL_OWNER] or '—'}")
                st.caption(
                    f"Status (raw): {raw[bulk_import.COL_STATUS] or '—'} · "
                    f"Period: {raw[bulk_import.COL_PERIOD] or '—'}"
                )
                if raw[bulk_import.COL_NOTES]:
                    st.caption(f"Notes: {raw[bulk_import.COL_NOTES]}")
            with c2:
                svc_default = next(
                    (k for k, v in service_options.items()
                     if v == (r["service"]["code"] if r["service"] else None)),
                    "— no match, choose manually —",
                )
                svc_label = "Service" + (" ⚠️ needs review" if r["service_confidence"] != "exact" else "")
                svc_choice = st.selectbox(
                    svc_label, options=list(service_options.keys()),
                    index=list(service_options.keys()).index(svc_default),
                    key=f"bulk_svc_{r['index']}",
                )
                owner_default = next(
                    (k for k, v in owner_options.items()
                     if v == (r["owner"]["id"] if r["owner"] else None)),
                    "— unassigned —",
                )
                owner_label = "Owner" + (" ⚠️ needs review" if r["owner_confidence"] != "exact" else "")
                owner_choice = st.selectbox(
                    owner_label, options=list(owner_options.keys()),
                    index=list(owner_options.keys()).index(owner_default),
                    key=f"bulk_owner_{r['index']}",
                )
                r["_final_service_code"] = service_options[svc_choice]
                r["_final_owner_id"] = owner_options[owner_choice]

    st.write("")
    if st.button(f"Commit import — create {len(preview)} job(s)", type="primary", key="bulk_commit"):
        resolved = [
            {
                "client_name": r["raw"][bulk_import.COL_CLIENT] or None,
                "service_code": r["_final_service_code"],
                "owner_id": r["_final_owner_id"],
                "title": r["title"],
                "note": r["note"],
            }
            for r in preview
        ]
        summary = bulk_import.commit_import(resolved, user["id"])
        st.session_state.pop(_PREVIEW_KEY, None)
        st.session_state.pop(_FILENAME_KEY, None)
        st.session_state[_RESULT_KEY] = summary
        # Force a fresh file_uploader widget instance so the just-imported
        # file isn't still "selected" (which would otherwise instantly
        # reparse and rebuild the same preview on this very rerun).
        st.session_state[_EPOCH_KEY] = st.session_state.get(_EPOCH_KEY, 0) + 1
        st.rerun()
