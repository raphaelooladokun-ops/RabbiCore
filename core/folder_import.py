"""Bulk client-folder upload (file_room_admin/super_admin): turn a CSV
export of an existing paper register into the client_folder catalogue in
one action — the master list of what folders/files physically exist per
client, independent of file_register's own check-out/return movement log.

One row per folder — a client with several folders repeats across several
rows, same shape as the CSV a real file-room register would naturally
export. Client is matched to an existing record where possible; anything
that doesn't match cleanly is flagged in the preview rather than silently
guessed at (same convention as core/bulk_import.py's own job upload), and
the preview always renders before anything is written to the database.
Category/Date Opened/Status are each best-effort normalised and never
block a row on their own — only the client match gets the full "needs
review" treatment, since it's the one relationship that has to be right."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from core import models

# Canonical column keys, matched against the CSV header case-insensitively
# with whitespace around "/" collapsed — tolerant of "Folder / File Name"
# vs "Folder/File Name" etc.
COL_CLIENT = "client"
COL_FOLDER_NAME = "folder/file name"
COL_CATEGORY = "category"
COL_LOCATION = "location"
COL_DATE_OPENED = "date opened"
COL_STATUS = "status"
COL_NOTES = "notes"

_REQUIRED = [COL_CLIENT, COL_FOLDER_NAME]
_OPTIONAL = [COL_CATEGORY, COL_LOCATION, COL_DATE_OPENED, COL_STATUS, COL_NOTES]
_ALL_COLS = _REQUIRED + _OPTIONAL
_EXPECTED_HEADER = "Client, Folder/File Name, Category, Location, Date Opened, Status, Notes"

# Category matches the same pillar vocabulary used everywhere else in the
# app (job.category / service_catalogue.pillar), so a folder can later be
# filtered the same way a job can.
_CATEGORY_MAP = {"cac": "cac", "immigration": "immigration", "cit": "cit", "state": "state", "other": "other"}
_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")


class FolderImportError(Exception):
    pass


def _normalize_header(h: str) -> str:
    h = re.sub(r"\s*/\s*", "/", h.strip().lower())
    return re.sub(r"\s+", " ", h)


def _decode(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FolderImportError("Could not read this file as text — is it a valid CSV?")


def parse_csv(raw_bytes: bytes) -> list[dict]:
    """Returns one dict per non-blank row, keyed by the canonical column
    names above (never the CSV's own header spelling). Only Client and
    Folder/File Name need to actually be present as columns — the other
    five are each optional at the header level, not just per-cell: a
    simpler register that never tracked, say, Location doesn't need an
    empty column just to satisfy the importer."""
    reader = csv.DictReader(io.StringIO(_decode(raw_bytes)))
    if not reader.fieldnames:
        raise FolderImportError("The file has no header row.")

    header_map = {_normalize_header(h): h for h in reader.fieldnames}
    missing = [c for c in _REQUIRED if c not in header_map]
    if missing:
        raise FolderImportError(
            f"Missing expected column(s): {', '.join(missing)}. "
            f"Expected columns: {_EXPECTED_HEADER}."
        )

    rows = []
    for raw in reader:
        row = {}
        for canon in _ALL_COLS:
            orig = header_map.get(canon)
            row[canon] = (raw.get(orig) or "").strip() if orig else ""
        if any(row.values()):
            rows.append(row)
    return rows


def _find_client(name: str, clients: list) -> dict | None:
    if not name:
        return None
    lname = name.lower()
    for c in clients:
        if c["name"].lower() == lname:
            return c
    return None


def _match_category(text: str) -> tuple[str | None, bool]:
    """(category_or_None, recognized) — blank is always "recognized" (just
    no category given); a non-blank value that doesn't match one of the
    five known pillars is flagged in the preview but never blocks the row,
    since it's a soft/optional field."""
    if not text:
        return None, True
    key = text.strip().lower()
    if key in _CATEGORY_MAP:
        return _CATEGORY_MAP[key], True
    return None, False


def _parse_date(text: str) -> tuple[object | None, bool]:
    if not text:
        return None, True
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), True
        except ValueError:
            continue
    return None, False


def _match_status(text: str) -> str:
    key = text.strip().lower()
    return key if key in models.FOLDER_STATUSES else "active"


def build_preview(rows: list[dict], clients: list) -> list[dict]:
    """One entry per CSV row: the raw fields plus whatever this row
    resolved to. client_confidence other than 'exact' is what the view
    flags for manual review before commit; category/date issues are
    surfaced as a soft count only."""
    preview = []
    for i, row in enumerate(rows):
        client = _find_client(row[COL_CLIENT], clients)
        category, cat_ok = _match_category(row[COL_CATEGORY])
        opened_date, date_ok = _parse_date(row[COL_DATE_OPENED])
        preview.append({
            "index": i,
            "raw": row,
            "client": client,
            "will_create_client": client is None and bool(row[COL_CLIENT]),
            "category": category,
            "category_recognized": cat_ok,
            "opened_date": opened_date,
            "date_recognized": date_ok,
            "status": _match_status(row[COL_STATUS]),
        })
    return preview


def commit_import(resolved_rows: list[dict], actor_id: int) -> dict:
    """resolved_rows: each {"client_name", "folder_name", "category",
    "location", "opened_date", "status", "notes"} — the view's final
    choice per row after any manual override. Creates at most one client
    per distinct new name, then one client_folder row per row with both a
    client name and a folder name; a row missing either is skipped and
    counted, never silently dropped without a trace."""
    client_cache: dict[str, int] = {c["name"].lower(): c["id"] for c in models.list_clients()}

    folders_created = 0
    clients_created = 0
    skipped = 0
    for row in resolved_rows:
        name = (row.get("client_name") or "").strip()
        folder_name = (row.get("folder_name") or "").strip()
        if not name or not folder_name:
            skipped += 1
            continue

        key = name.lower()
        if key not in client_cache:
            new_client = models.create_client(name)
            client_cache[key] = new_client["id"]
            clients_created += 1
        client_id = client_cache[key]

        models.create_client_folder(
            client_id, folder_name,
            category=row.get("category"), location=row.get("location") or None,
            opened_date=row.get("opened_date"), status=row.get("status") or "active",
            notes=row.get("notes") or None, created_by=actor_id,
        )
        folders_created += 1

    return {"folders_created": folders_created, "clients_created": clients_created, "skipped": skipped}
