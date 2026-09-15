"""Bulk job upload (super admin): turn a CSV of legacy/external job records
into real jobs in one action. Every imported job lands as status = New,
regardless of what the CSV's own Status column says — that column (plus
Period/Details and Notes) is folded into the job's internal notes instead,
as context, so nothing from the source file is lost. Client/service/owner
are matched to existing records where possible; anything that doesn't match
cleanly is flagged in the preview rather than silently guessed at, and the
preview always renders before anything is written to the database."""

from __future__ import annotations

import csv
import io
import re

from core import models
from core.constants import SOURCE_BULK_IMPORT
from core.seed_data import PILLAR_TO_CATEGORY

# Canonical column keys, matched against the CSV header case-insensitively
# with whitespace around "/" collapsed — tolerant of "Owner / Source" vs
# "Owner/Source" etc.
COL_OWNER = "owner/source"
COL_STATUS = "status"
COL_CLIENT = "client/company"
COL_SERVICE = "service/task"
COL_PERIOD = "period/details"
COL_NOTES = "notes"

_REQUIRED = [COL_OWNER, COL_STATUS, COL_CLIENT, COL_SERVICE, COL_PERIOD, COL_NOTES]
_EXPECTED_HEADER = "Owner/Source, Status, Client/Company, Service/Task, Period/Details, Notes"


class BulkImportError(Exception):
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
    raise BulkImportError("Could not read this file as text — is it a valid CSV?")


def parse_csv(raw_bytes: bytes) -> list[dict]:
    """Returns one dict per non-blank row, keyed by the canonical column
    names above (never the CSV's own header spelling)."""
    reader = csv.DictReader(io.StringIO(_decode(raw_bytes)))
    if not reader.fieldnames:
        raise BulkImportError("The file has no header row.")

    header_map = {_normalize_header(h): h for h in reader.fieldnames}
    missing = [c for c in _REQUIRED if c not in header_map]
    if missing:
        raise BulkImportError(
            f"Missing expected column(s): {', '.join(missing)}. "
            f"Expected columns: {_EXPECTED_HEADER}."
        )

    rows = []
    for raw in reader:
        row = {canon: (raw.get(orig) or "").strip() for canon, orig in header_map.items()}
        if any(row.values()):
            rows.append(row)
    return rows


def build_note(status: str, period: str, notes: str) -> str | None:
    parts = []
    if status:
        parts.append(f"Imported status: {status}")
    if period:
        parts.append(f"Period: {period}")
    if notes:
        parts.append(notes)
    return " | ".join(parts) if parts else None


def _find_client(name: str, clients: list) -> dict | None:
    if not name:
        return None
    lname = name.lower()
    for c in clients:
        if c["name"].lower() == lname:
            return c
    return None


def _find_service(text: str, services: list) -> tuple[dict | None, str]:
    """(service_or_None, confidence): 'exact' / 'fuzzy' / 'none'. Fuzzy means
    exactly one service name contains the CSV text or vice versa — still
    surfaced for review, never treated as a silent match."""
    if not text:
        return None, "none"
    ltext = text.lower()
    for s in services:
        if s["name"].lower() == ltext:
            return s, "exact"
    candidates = [s for s in services if ltext in s["name"].lower() or s["name"].lower() in ltext]
    if len(candidates) == 1:
        return candidates[0], "fuzzy"
    return None, "none"


def _find_owner(text: str, staff: list) -> tuple[dict | None, str]:
    if not text:
        return None, "none"
    ltext = text.lower()
    for s in staff:
        if s["name"].lower() == ltext:
            return s, "exact"
    candidates = [
        s for s in staff
        if s["name"].split()[0].lower() == ltext or ltext in s["name"].lower()
    ]
    if len(candidates) == 1:
        return candidates[0], "fuzzy"
    return None, "none"


def build_preview(rows: list[dict], clients: list, services: list, staff: list) -> list[dict]:
    """One entry per CSV row: the raw fields plus whatever this row resolved
    to. `service_confidence`/`owner_confidence` other than 'exact' is what
    the view flags for manual review before commit."""
    preview = []
    for i, row in enumerate(rows):
        client = _find_client(row[COL_CLIENT], clients)
        service, svc_conf = _find_service(row[COL_SERVICE], services)
        owner, owner_conf = _find_owner(row[COL_OWNER], staff)
        preview.append({
            "index": i,
            "raw": row,
            "client": client,
            "will_create_client": client is None and bool(row[COL_CLIENT]),
            "service": service,
            "service_confidence": svc_conf,
            "owner": owner,
            "owner_confidence": owner_conf,
            "note": build_note(row[COL_STATUS], row[COL_PERIOD], row[COL_NOTES]),
            "title": row[COL_SERVICE] or row[COL_CLIENT] or "Imported job",
        })
    return preview


def commit_import(resolved_rows: list[dict], actor_id: int) -> dict:
    """resolved_rows: each {"client_name", "service_code", "owner_id",
    "title", "note"} — the view's final choice per row after any manual
    override. Creates at most one client per distinct new name, then one
    job per row, every job landing as status = New. Returns a summary for
    the confirmation toast."""
    client_cache: dict[str, int] = {c["name"].lower(): c["id"] for c in models.list_clients()}

    jobs_created = 0
    clients_created = 0
    for row in resolved_rows:
        client_id = None
        name = (row.get("client_name") or "").strip()
        if name:
            key = name.lower()
            if key not in client_cache:
                new_client = models.create_client(name)
                client_cache[key] = new_client["id"]
                clients_created += 1
            client_id = client_cache[key]

        service_code = row.get("service_code")
        category = "front_office"
        if service_code:
            service = models.get_service(service_code)
            if service:
                category = PILLAR_TO_CATEGORY[service["pillar"]]

        models.create_job(
            client_id=client_id,
            category=category,
            service_type=service_code,
            title=row["title"] or "Imported job",
            description=None,
            owner_id=row.get("owner_id"),
            source=SOURCE_BULK_IMPORT,
            created_by=actor_id,
            sla_date=None,
            attributes={},
            waiting_on_client=None,
            internal_notes=row.get("note"),
        )
        jobs_created += 1

    return {"jobs_created": jobs_created, "clients_created": clients_created}
