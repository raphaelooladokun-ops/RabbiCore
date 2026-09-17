"""Super-admin-only staff account management: create a real login for a new
member of staff (with a generated username/password shown once, ready to
copy and hand over), reset an existing user's password on demand, and
deactivate/reactivate accounts. Never a hard delete — a deactivated
account's jobs, comments and invoices stay exactly where they are,
attributed to them.

Passwords are never stored anywhere they could be shown again later —
bcrypt hashing is one-way by design. "I need working credentials for this
person again" is answered by resetting (a fresh password, shown once, same
as creation), not by keeping a retrievable register of old ones."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import (
    CATEGORY_LABELS,
    ROLE_ADMIN,
    ROLE_LABELS,
    ROLE_MANAGER,
    ROLE_PRINCIPAL,
    ROLE_SPECIALIST,
    ROLE_SUPER_ADMIN,
)

_CREATABLE_ROLES = [ROLE_PRINCIPAL, ROLE_MANAGER, ROLE_ADMIN, ROLE_SPECIALIST]
_SPECIALITY_CATEGORIES = ["immigration", "cit", "state"]
_CREDENTIALS_KEY = "new_user_credentials"


def render(user: dict) -> None:
    ui.page_header("Users", "Create staff logins and manage who has access.")

    _credentials_banner()
    _create_user_form()
    st.divider()
    _users_list()
    st.divider()
    _merge_duplicates_control()


def _credentials_banner() -> None:
    creds = st.session_state.get(_CREDENTIALS_KEY)
    if not creds:
        return
    with st.container(border=True):
        if creds.get("kind") == "reset":
            st.success(
                f"**{creds['name']}**'s password has been reset — copy the new one now, it won't be shown again. "
                "Their old password no longer works."
            )
        else:
            st.success(f"**{creds['name']}**'s login is ready — copy these now, they won't be shown again.")
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Username")
            st.code(creds["username"], language=None)
        with c2:
            st.caption("Password")
            st.code(creds["password"], language=None)
        if st.button("I've copied this — dismiss", key="dismiss_creds"):
            st.session_state.pop(_CREDENTIALS_KEY, None)
            st.rerun()


def _create_user_form() -> None:
    st.markdown("#### Create user")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Full name *", placeholder="e.g. Amaka Bello", key="cu_name")
    with col2:
        role = st.selectbox(
            "Role *", options=_CREATABLE_ROLES, format_func=lambda r: ROLE_LABELS[r], key="cu_role",
        )

    speciality = None
    if role == ROLE_SPECIALIST:
        speciality = st.selectbox(
            "Speciality / module *", options=_SPECIALITY_CATEGORIES, index=None,
            placeholder="Select…", format_func=lambda c: CATEGORY_LABELS[c], key="cu_speciality",
        )
        st.caption("Jobs in this module float this specialist to the top of the Owner list in Capture.")

    duplicate = models.find_active_staff_by_name(name.strip()) if name.strip() else None
    create_anyway = True
    if duplicate:
        st.warning(
            f"⚠️ An active user named **{ui.staff_label_md(duplicate)}** already exists — "
            "check this is genuinely a different person before creating another."
        )
        create_anyway = st.checkbox("Create anyway — this is a different person", key="cu_createanyway")

    if st.button("Create user", type="primary", key="cu_submit"):
        if not name.strip():
            st.error("Enter the person's full name.")
            return
        if role == ROLE_SPECIALIST and not speciality:
            st.error("Select a speciality/module for the specialist.")
            return
        if duplicate and not create_anyway:
            st.error("Confirm this isn't a duplicate — check 'Create anyway' to continue.")
            return

        result = models.create_staff_account(name.strip(), role)
        if role == ROLE_SPECIALIST and speciality:
            models.assign_module_specialist(speciality, result["staff"]["id"])

        st.session_state[_CREDENTIALS_KEY] = {
            "kind": "created",
            "name": result["staff"]["name"],
            "username": result["username"],
            "password": result["password"],
        }
        st.rerun()


_ROW_WIDTHS = [2.0, 1.2, 0.7, 0.7, 0.5, 0.8, 1.0, 1.1, 0.8]


def _users_list() -> None:
    st.markdown("#### All users")
    st.caption(
        "Lost or need to hand out a password again? Use **Reset password** — it issues a fresh one "
        "on the spot; the old one is never stored anywhere it could be looked up later. The "
        "**Username** column is what they log in with — it tells apart two people who happen to "
        "share a name."
    )
    staff = [s for s in models.list_staff(active_only=False) if s["role"] != "client"]
    categories_by_staff = models.list_staff_categories()

    header = st.columns(_ROW_WIDTHS)
    for col, label in zip(header, ["Username", "Name", "Role", "Speciality", "Status", "Created", "", "", ""]):
        col.markdown(f"**{label}**")

    for s in staff:
        cols = st.columns(_ROW_WIDTHS)
        cols[0].write(f"`{s['email']}`")
        cols[1].write(s["name"])
        cols[2].write(ROLE_LABELS.get(s["role"], s["role"]))
        specialities = categories_by_staff.get(s["id"], [])
        cols[3].write(", ".join(CATEGORY_LABELS.get(c, c) for c in specialities) if specialities else "—")
        cols[4].write("Active" if s["active"] else "Inactive")
        cols[5].write(s["created_at"].strftime("%d %b %Y, %H:%M") if s.get("created_at") else "—")

        if s["role"] == ROLE_SUPER_ADMIN:
            cols[6].caption("—")
            cols[7].caption("—")
            cols[8].caption("—")
            continue

        if s["active"]:
            if cols[6].button("Deactivate", key=f"deact_{s['id']}"):
                models.set_staff_active(s["id"], False)
                st.toast(f"{s['name']} deactivated.", icon="✅")
                st.rerun()
        else:
            if cols[6].button("Reactivate", key=f"react_{s['id']}"):
                models.set_staff_active(s["id"], True)
                st.toast(f"{s['name']} reactivated.", icon="✅")
                st.rerun()

        if cols[7].button("Reset password", key=f"reset_{s['id']}"):
            result = models.reset_staff_password(s["id"])
            st.session_state[_CREDENTIALS_KEY] = {
                "kind": "reset",
                "name": result["staff"]["name"],
                "username": result["staff"]["email"],
                "password": result["password"],
            }
            st.rerun()

        confirm_key = f"confirm_del_{s['id']}"
        if cols[8].button("Delete", key=f"del_{s['id']}"):
            st.session_state[confirm_key] = True
            st.rerun()

        if st.session_state.get(confirm_key):
            with st.container(border=True):
                st.warning(
                    f"Permanently delete **{s['name']}**? This is the harder, permanent action — "
                    "it cannot be undone. Deactivate instead to keep the record but block their login."
                )
                c1, c2 = st.columns(2)
                if c1.button("Yes, delete permanently", key=f"confirmdel_{s['id']}", type="primary"):
                    try:
                        models.delete_staff(s["id"])
                    except models.StaffDeleteError as e:
                        st.error(str(e))
                    else:
                        st.session_state.pop(confirm_key, None)
                        st.toast(f"{s['name']} permanently deleted.", icon="✅")
                        st.rerun()
                if c2.button("Cancel", key=f"canceldel_{s['id']}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


def _merge_duplicates_control() -> None:
    st.markdown("#### Merge duplicate users")
    st.caption(
        "If the same person was created twice, merge the extra account into the one to keep — "
        "every job, invoice, expense, comment and specialist assignment moves over, then the "
        "duplicate is permanently removed. This cannot be undone."
    )
    staff = [s for s in models.list_staff(active_only=False) if s["role"] != "client"]
    if len(staff) < 2:
        st.caption("Nothing to merge — fewer than two users on file.")
        return

    options = {ui.staff_label(s): s["id"] for s in staff}
    c1, c2 = st.columns(2)
    keep_label = c1.selectbox(
        "Keep", options=list(options.keys()), index=None, placeholder="Select…", key="merge_keep",
    )
    remove_label = c2.selectbox(
        "Remove (merge into Keep)", options=list(options.keys()), index=None, placeholder="Select…",
        key="merge_remove",
    )

    if keep_label and remove_label and keep_label == remove_label:
        st.error("Pick two different users.")
        return

    confirm = False
    if keep_label and remove_label:
        confirm = st.checkbox(
            f"Yes, merge {remove_label} into {keep_label} and permanently delete {remove_label} — "
            "this cannot be undone.",
            key="merge_confirm",
        )

    if st.button("Merge", key="merge_btn", disabled=not (keep_label and remove_label and confirm)):
        models.merge_staff(options[remove_label], options[keep_label])
        st.session_state.pop("merge_confirm", None)
        st.toast(f"Merged {remove_label} into {keep_label}.", icon="✅")
        st.rerun()
