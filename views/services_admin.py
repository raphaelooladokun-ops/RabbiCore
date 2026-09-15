"""Service catalogue management — admin/super_admin only. Add a new
service to the catalogue; it becomes selectable in Capture immediately,
exactly like any of the original 43. A service that doesn't fit the 4
locked pillars goes under a 5th, "Other Services" pillar/category."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import CATEGORY_LABELS
from core.seed_data import PILLAR_TO_CATEGORY

_PILLARS = ["CAC", "Immigration", "CIT", "State", "Other"]


def render(user: dict) -> None:
    ui.page_header("Services", "Add a new service to the catalogue — it's selectable in Capture right away.")

    _create_service_form()
    st.divider()
    _catalogue_list()


def _create_service_form() -> None:
    st.markdown("#### Add a service")
    col1, col2 = st.columns([2, 1])
    with col1:
        name = st.text_input("Service name *", placeholder="e.g. Trademark Registration", key="svc_name")
    with col2:
        pillar = st.selectbox(
            "Pillar *", options=_PILLARS, key="svc_pillar",
            help="Doesn't fit CAC/Immigration/CIT/State? Choose Other — it gets its own category.",
        )

    if pillar == "Other":
        st.caption("This service will appear under the **Other Services** category.")

    if st.button("Add service", type="primary", key="svc_submit"):
        try:
            service = models.create_service(name, pillar)
        except models.ServiceCreateError as e:
            st.error(str(e))
        else:
            st.success(f"**{service['name']}** added — code `{service['code']}`. Selectable in Capture now.")
            st.rerun()


def _catalogue_list() -> None:
    st.markdown("#### Full catalogue")
    services = models.list_service_catalogue()
    by_pillar: dict = {}
    for s in services:
        by_pillar.setdefault(s["pillar"], []).append(s)

    for pillar in _PILLARS:
        rows = by_pillar.get(pillar, [])
        if not rows:
            continue
        category = PILLAR_TO_CATEGORY[pillar]
        st.markdown(f"**{CATEGORY_LABELS.get(category, pillar)}** ({len(rows)})")
        for s in rows:
            st.caption(f"{s['name']} — `{s['code']}`" + (" · recurring" if s.get("recurring_frequency") else ""))
        st.write("")
