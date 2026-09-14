"""super_admin only: every job that's been hidden — invisible everywhere
else in the app, kept here so nothing is ever silently gone. Unhide brings
a job straight back into every normal view and count; Delete removes it
for good, same permanent action as on the job's own detail page."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import CATEGORY_LABELS, STATUS_LABELS_SHORT, humanize

_ROW_WIDTHS = [1.1, 1.6, 2.2, 1.2, 1.1, 1.6, 1.1, 1.3]
_HEADERS = ["Job ID", "Client", "What", "Category", "Status", "Hidden", "", ""]


def render(user: dict) -> None:
    ui.page_header("Hidden Jobs", "Invisible everywhere else — restore or permanently delete from here.")

    jobs = models.list_hidden_jobs()
    if not jobs:
        st.caption("Nothing hidden right now.")
        return

    header = st.columns(_ROW_WIDTHS)
    for col, label in zip(header, _HEADERS):
        col.markdown(f"**{label}**")

    for j in jobs:
        cols = st.columns(_ROW_WIDTHS)
        cols[0].write(ui.short_job_id(j["job_id"]))
        cols[1].write(j.get("client_name") or "—")
        cols[2].write(j["title"])
        cols[3].write(humanize(j["category"], CATEGORY_LABELS))
        cols[4].write(STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])))
        cols[5].write(j["hidden_at"].strftime("%d %b %Y, %H:%M") if j.get("hidden_at") else "—")

        if cols[6].button("Unhide", key=f"unhide_{j['id']}"):
            models.unhide_job(j["id"])
            st.toast(f"{j['job_id']} unhidden — back in the register.", icon="✅")
            st.rerun()

        confirm_key = f"delconfirm_{j['id']}"
        with cols[7].popover("Delete…"):
            st.caption("This cannot be undone.")
            confirm = st.checkbox("I understand — delete permanently", key=confirm_key)
            if st.button("Delete permanently", key=f"del_{j['id']}", disabled=not confirm, type="primary"):
                try:
                    models.delete_job(j["id"])
                except models.JobDeleteError as e:
                    st.error(str(e))
                else:
                    st.toast(f"{j['job_id']} permanently deleted.", icon="✅")
                    st.rerun()

    st.caption(f"{len(jobs)} hidden job(s)")
