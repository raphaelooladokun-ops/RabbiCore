"""File Register: which client file is currently out, for which job, who
has it, and the full check-out/return history — plus the client folder
catalogue, the master list of what folders/files physically exist per
client (independent of any movement). file_room_admin is the only role
that logs a check-out — EC, manager, admin and super_admin see the same
dashboard read-only (models.can_write_file_register). The folder
catalogue itself is writable by file_room_admin and super_admin
(models.can_write_client_folders), single entries or bulk via CSV."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from core import folder_import, models
from core import ui
from core.constants import CATEGORY_LABELS, titlecase_name

_FOLDER_WIDTHS = [1.6, 2.0, 1.2, 1.5, 1.2, 1.0, 2.0]
_FOLDER_CATEGORY_OPTIONS = ["CAC", "Immigration", "CIT", "State", "Other"]
_FOLDER_CATEGORY_TO_VALUE = {"CAC": "cac", "Immigration": "immigration", "CIT": "cit", "State": "state", "Other": "other"}
_FOLDER_STATUS_OPTIONS = ["Active", "Archived", "Closed"]

_FOLDER_PREVIEW_KEY = "folder_upload_preview"
_FOLDER_FILENAME_KEY = "_folder_upload_filename"
_FOLDER_RESULT_KEY = "folder_upload_result"
_FOLDER_EPOCH_KEY = "_folder_upload_epoch"

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
    st.divider()
    _client_folders(user)


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


# ---------------------------------------------------------------------------
# Client folders — the master catalogue of what physically exists per
# client, separate from the check-out/return log above. Single entries and
# a CSV bulk upload (an existing paper register, one row per folder).
# ---------------------------------------------------------------------------
def _client_folders(user: dict) -> None:
    st.markdown("#### Client folders on file")
    st.caption(
        "What folders/files physically exist for each client — independent of the check-out log above, "
        "which only tracks movement, not what's actually on the shelf."
    )

    clients = models.list_clients(active_only=True)
    client_map = {titlecase_name(c["name"]): c for c in clients}
    client_choice = st.selectbox(
        "Filter by client", options=["All clients"] + list(client_map.keys()), key="cf_filter_client",
    )
    client_id = client_map[client_choice]["id"] if client_choice != "All clients" else None

    folders = models.list_client_folders(client_id)
    if not folders:
        st.caption("No folders on file for this client yet." if client_id else "No folders on file yet.")
    else:
        header = st.columns(_FOLDER_WIDTHS)
        labels = ["Client", "Folder / File Name", "Category", "Location", "Opened", "Status", "Notes"]
        for col, label in zip(header, labels):
            col.markdown(f"**{label}**")
        for f in folders:
            cols = st.columns(_FOLDER_WIDTHS)
            if cols[0].button(titlecase_name(f["client_name"]), key=f"cffclient_{f['id']}", type="tertiary"):
                ui.go_to_client(f["client_id"])
            cols[1].write(f["folder_name"])
            cols[2].write(CATEGORY_LABELS.get(f["category"], "—") if f.get("category") else "—")
            cols[3].write(f.get("location") or "—")
            cols[4].write(f["opened_date"].isoformat() if f.get("opened_date") else "—")
            cols[5].write((f.get("status") or "active").title())
            cols[6].write(f.get("notes") or "—")

    if models.can_write_client_folders(user):
        st.write("")
        _add_folder_control(user)
        _bulk_folder_upload(user)


def _add_folder_control(user: dict) -> None:
    with st.expander("+ Add a folder"):
        clients = models.list_clients(active_only=True)
        client_map = {titlecase_name(c["name"]): c for c in clients}
        client_name = st.selectbox(
            "Client *", options=list(client_map.keys()), index=None,
            placeholder="Select client…", key="cf_add_client",
        )
        folder_name = st.text_input("Folder / file name *", key="cf_add_name", placeholder="e.g. Main client file")

        c1, c2, c3 = st.columns(3)
        with c1:
            category_choice = st.selectbox("Category", options=["—"] + _FOLDER_CATEGORY_OPTIONS, key="cf_add_category")
        with c2:
            location = st.text_input("Location", key="cf_add_location", placeholder="e.g. Cabinet 3, Shelf B")
        with c3:
            opened_date = st.date_input("Date opened", value=None, key="cf_add_opened")

        c4, c5 = st.columns(2)
        with c4:
            status_choice = st.selectbox("Status", options=_FOLDER_STATUS_OPTIONS, key="cf_add_status")
        with c5:
            notes = st.text_input("Notes", key="cf_add_notes")

        if st.button("Add folder", key="cf_add_submit", type="primary"):
            if not (client_name and folder_name.strip()):
                st.error("Client and folder/file name are required.")
            else:
                models.create_client_folder(
                    client_map[client_name]["id"], folder_name.strip(),
                    category=_FOLDER_CATEGORY_TO_VALUE.get(category_choice),
                    location=location.strip() or None, opened_date=opened_date,
                    status=status_choice.lower(), notes=notes.strip() or None, created_by=user["id"],
                )
                st.toast("Folder added.", icon="✅")
                st.rerun()


def _bulk_folder_upload(user: dict) -> None:
    with st.expander("+ Bulk upload folder register"):
        result = st.session_state.pop(_FOLDER_RESULT_KEY, None)
        if result:
            msg = (
                f"Imported {result['folders_created']} folder(s), created {result['clients_created']} "
                f"new client(s)."
            )
            if result["skipped"]:
                msg += f" Skipped {result['skipped']} row(s) missing a client or folder name."
            st.success(msg)

        st.caption(
            "Expected columns: **Client, Folder/File Name, Category, Location, Date Opened, Status, Notes** "
            "— only Client and Folder/File Name are required; leave the rest out entirely if you don't track "
            "them. One row per folder — a client with several folders repeats across several rows."
        )

        epoch = st.session_state.get(_FOLDER_EPOCH_KEY, 0)
        uploaded = st.file_uploader("CSV file", type=["csv"], key=f"folder_upload_file_{epoch}")
        if uploaded is not None and st.session_state.get(_FOLDER_FILENAME_KEY) != uploaded.name:
            _load_folder_preview(uploaded)

        preview = st.session_state.get(_FOLDER_PREVIEW_KEY)
        if preview:
            _render_folder_preview(preview, user)


def _load_folder_preview(uploaded) -> None:
    try:
        rows = folder_import.parse_csv(uploaded.getvalue())
    except folder_import.FolderImportError as e:
        st.error(str(e))
        st.session_state.pop(_FOLDER_PREVIEW_KEY, None)
        st.session_state.pop(_FOLDER_FILENAME_KEY, None)
        return

    if not rows:
        st.warning("No data rows found in that file.")
        st.session_state.pop(_FOLDER_PREVIEW_KEY, None)
        st.session_state.pop(_FOLDER_FILENAME_KEY, None)
        return

    clients = models.list_clients()
    st.session_state[_FOLDER_PREVIEW_KEY] = folder_import.build_preview(rows, clients)
    st.session_state[_FOLDER_FILENAME_KEY] = uploaded.name


def _render_folder_preview(preview: list, user: dict) -> None:
    new_client_names = {r["raw"][folder_import.COL_CLIENT] for r in preview if r["will_create_client"]}
    no_client = [r for r in preview if not r["raw"][folder_import.COL_CLIENT]]
    unrecognized_category = [r for r in preview if not r["category_recognized"]]
    unrecognized_date = [r for r in preview if not r["date_recognized"]]

    st.write("")
    st.markdown("#### Preview")
    m1, m2, m3 = st.columns(3)
    m1.metric("Folders to create", len(preview) - len(no_client))
    m2.metric("New clients", len(new_client_names))
    m3.metric("Rows to skip (no client)", len(no_client))

    soft_notes = []
    if unrecognized_category:
        soft_notes.append(f"{len(unrecognized_category)} row(s) had an unrecognized Category — left blank")
    if unrecognized_date:
        soft_notes.append(f"{len(unrecognized_date)} row(s) had a Date Opened that couldn't be read — left blank")
    if soft_notes:
        st.caption(" · ".join(soft_notes))

    for r in preview:
        raw = r["raw"]
        with st.container(border=True):
            client_label = raw[folder_import.COL_CLIENT] or "— no client, will be skipped —"
            if r["will_create_client"]:
                st.write(f"**{client_label}**  🆕 new client")
            elif r["client"]:
                st.write(f"**{client_label}**  ✅ matched")
            else:
                st.write(f"**{client_label}**  ⚠️ skipped")
            detail = f"Folder: {raw[folder_import.COL_FOLDER_NAME] or '—'}"
            if raw[folder_import.COL_LOCATION]:
                detail += f" · Location: {raw[folder_import.COL_LOCATION]}"
            if r["opened_date"]:
                detail += f" · Opened: {r['opened_date'].isoformat()}"
            st.caption(detail)
            if raw[folder_import.COL_NOTES]:
                st.caption(f"Notes: {raw[folder_import.COL_NOTES]}")

    st.write("")
    to_create = len(preview) - len(no_client)
    if st.button(
        f"Commit import — create {to_create} folder(s)", type="primary", key="folder_commit",
        disabled=to_create == 0,
    ):
        resolved = [
            {
                "client_name": r["raw"][folder_import.COL_CLIENT],
                "folder_name": r["raw"][folder_import.COL_FOLDER_NAME],
                "category": r["category"],
                "location": r["raw"][folder_import.COL_LOCATION],
                "opened_date": r["opened_date"],
                "status": r["status"],
                "notes": r["raw"][folder_import.COL_NOTES],
            }
            for r in preview
        ]
        summary = folder_import.commit_import(resolved, user["id"])
        st.session_state.pop(_FOLDER_PREVIEW_KEY, None)
        st.session_state.pop(_FOLDER_FILENAME_KEY, None)
        st.session_state[_FOLDER_RESULT_KEY] = summary
        # Force a fresh file_uploader widget instance so the just-imported
        # file isn't still "selected" (which would otherwise instantly
        # reparse and rebuild the same preview on this very rerun).
        st.session_state[_FOLDER_EPOCH_KEY] = st.session_state.get(_FOLDER_EPOCH_KEY, 0) + 1
        st.rerun()
