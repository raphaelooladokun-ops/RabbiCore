"""Specialist home: just their queue — already-tracked jobs, never a loose
message. They can advance status and mark jobs blocked."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from views import register


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {user['name'].split()[0]}", "Jobs assigned to you — never a loose message.")

    jobs = models.list_jobs(owner_id=user["id"], exclude_dismissed=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("New", sum(1 for j in jobs if j["status"] == "new"))
    c2.metric("In progress", sum(1 for j in jobs if j["status"] == "in_progress"))
    c3.metric("Blocked", sum(1 for j in jobs if j["status"] == "blocked"))
    c4.metric("Done", sum(1 for j in jobs if j["status"] == "done"))
    st.write("")

    register.render(user, only_own=True, show_header=False)
