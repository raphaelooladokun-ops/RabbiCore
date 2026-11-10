"""File Register: which client file is currently out, for which job, who
has it, and the full check-out/return history. file_room_admin is the only
role that makes entries here — EC, manager, admin and super_admin see the
same dashboard read-only (models.can_write_file_register)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from core import models
from core import ui
from core.constants import titlecase_name

_OUT_WIDTHS = [1.5, 1.3, 1.8, 1.3, 1.2, 0.7, 1.7]
_HISTORY_WIDTHS = [1.6, 1.4, 1.8, 1.4, 1.4, 1.4, 0.9]


def _format_folders(raw: str | None) -> str:
    """Folder/file names are typed one per line (more than one for a job
    that needs several) — tables are narrow, so the list view shows them
    joined compactly rather than wrapping across several row-heights."""
    if not raw:
        return "—"
    names = [line.strip() for line in raw.splitlines() if line.strip()]
    return ", ".join(names) if names else "—"


def render(user: dict) -> None:
    ui.page_header("File Register", "Which client file is out, for which job, and who has it.")
    can_write = models.can_write_file_register(user)

    _currently_out(can_write)
    st.divider()
    if can_write:
        _new_checkout(user)
        st.divider()
    _history()


def _currently_out(can_write: bool) -> None:
    st.markdown("#### Currently out")
    entries = models.list_currently_out_files()
    if not entries:
        st.caption("Nothing checked out right now.")
        return

    header = st.columns(_OUT_WIDTHS)
    for col, label in zip(header, ["Client", "Job", "Folder(s)", "Collected by", "Out since", "Days out", ""]):
        col.markdown(f"**{label}**")

    now = datetime.now(timezone.utc)
    for e in entries:
        cols = st.columns(_OUT_WIDTHS)
        if cols[0].button(titlecase_name(e["client_name"]), key=f"fileclient_out_{e['id']}", type="tertiary"):
            ui.go_to_client(e["client_id"])
        cols[1].write(f"{e['job_code']} — {e['job_title']}")
        cols[2].write(_format_folders(e.get("folder_names")))
        cols[3].write(titlecase_name(e["collected_by_name"]))
        cols[4].write(e["out_at"].strftime("%d %b %Y, %H:%M"))
        cols[5].write(str((now - e["out_at"]).days))
        if can_write:
            if cols[6].button("Mark returned", key=f"filereturn_{e['id']}"):
                models.mark_file_returned(e["id"])
                st.toast("Marked returned.", icon="✅")
                st.rerun()
        else:
            cols[6].write("—")


def _new_checkout(user: dict) -> None:
    with st.expander("+ Check out a file"):
        clients = models.list_clients(active_only=True)
        client_map = {titlecase_name(c["name"]): c for c in clients}
        client_name = st.selectbox(
            "Client *", options=list(client_map.keys()), index=None,
            placeholder="Select client…", key="fr_client",
        )

        job_map: dict = {}
        job_name = None
        if client_name:
            jobs = models.list_jobs(client_id=client_map[client_name]["id"])
            job_map = {f"{j['job_id']} — {j['title']}": j for j in jobs}
            if not job_map:
                st.caption("This client has no jobs on file yet.")
            else:
                job_name = st.selectbox(
                    "Job *", options=list(job_map.keys()), index=None,
                    placeholder="Which job is this file for?", key="fr_job",
                )

        staff = [s for s in models.list_staff(active_only=True) if s["role"] != "client"]
        staff_map = {ui.staff_label(s): s for s in staff}
        collector_name = st.selectbox(
            "Staff member collecting *", options=list(staff_map.keys()), index=None,
            placeholder="Who is taking this file?", key="fr_collector",
        )

        folder_names = st.text_area(
            "Folder / file name(s) *", key="fr_folders", placeholder="e.g. Main client file\nIMM — E-CERPAC sub-file",
            help="One folder or file per line — a job can need more than one out at once.",
        )

        if st.button("Check out", key="fr_checkout", type="primary"):
            if not (client_name and job_name and collector_name and folder_names.strip()):
                st.error("Client, job, collecting staff member and at least one folder/file name are required.")
            else:
                models.checkout_file(
                    client_map[client_name]["id"], job_map[job_name]["id"], staff_map[collector_name]["id"],
                    folder_names.strip(), logged_by=user["id"],
                )
                st.toast("File checked out.", icon="✅")
                st.rerun()


def _history() -> None:
    st.markdown("#### History")
    entries = models.list_file_register()
    if not entries:
        st.caption("No file movements logged yet.")
        return

    header = st.columns(_HISTORY_WIDTHS)
    for col, label in zip(header, ["Client", "Job", "Folder(s)", "Collected by", "Out", "Returned", "Status"]):
        col.markdown(f"**{label}**")

    for e in entries:
        cols = st.columns(_HISTORY_WIDTHS)
        if cols[0].button(titlecase_name(e["client_name"]), key=f"fileclient_hist_{e['id']}", type="tertiary"):
            ui.go_to_client(e["client_id"])
        cols[1].write(f"{e['job_code']} — {e['job_title']}")
        cols[2].write(_format_folders(e.get("folder_names")))
        cols[3].write(titlecase_name(e["collected_by_name"]))
        cols[4].write(e["out_at"].strftime("%d %b %Y, %H:%M"))
        cols[5].write(e["returned_at"].strftime("%d %b %Y, %H:%M") if e["returned_at"] else "—")
        status = models.file_entry_status(e)
        cols[6].write("Out" if status == models.FILE_STATUS_OUT else "Returned")
