"""Bulk compliance-item upload: turn a CSV of tracked documents (CERPAC
cards, quota approvals, tax clearance certificates, ...) into compliance_item
rows in one action. Client is matched to an existing record by exact name —
never auto-created, since a compliance item makes no sense for a client that
doesn't already exist in the system. Dates are DD/MM/YYYY; anything that
doesn't parse is flagged for manual entry in the preview, never silently
dropped or guessed at. Deliberately narrow fields: document type, position,
subject name and the two dates — no file attachments, no ID/passport
numbers. That's a policy choice, not a schema limitation to work around."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

# Canonical column keys, matched against the CSV header case-insensitively
# with surrounding whitespace collapsed.
COL_CLIENT = "client"
COL_DOCTYPE = "document type"
COL_POSITION = "position"
COL_NAME = "name"
COL_ISSUE = "issue date"
COL_EXPIRY = "expiry date"

_REQUIRED = [COL_CLIENT, COL_DOCTYPE, COL_POSITION, COL_NAME, COL_ISSUE, COL_EXPIRY]
_EXPECTED_HEADER = "Client, Document Type, Position, Name, Issue Date, Expiry Date"
_DATE_FORMAT = "%d/%m/%Y"


class ComplianceBulkImportError(Exception):
    pass


def _normalize_header(h: str) -> str:
    return " ".join(h.strip().lower().split())


def _decode(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ComplianceBulkImportError("Could not read this file as text — is it a valid CSV?")


def parse_csv(raw_bytes: bytes) -> list[dict]:
    """Returns one dict per non-blank row, keyed by the canonical column
    names above (never the CSV's own header spelling)."""
    reader = csv.DictReader(io.StringIO(_decode(raw_bytes)))
    if not reader.fieldnames:
        raise ComplianceBulkImportError("The file has no header row.")

    header_map = {_normalize_header(h): h for h in reader.fieldnames}
    missing = [c for c in _REQUIRED if c not in header_map]
    if missing:
        raise ComplianceBulkImportError(
            f"Missing expected column(s): {', '.join(missing)}. "
            f"Expected columns: {_EXPECTED_HEADER}."
        )

    rows = []
    for raw in reader:
        row = {canon: (raw.get(orig) or "").strip() for canon, orig in header_map.items()}
        if any(row.values()):
            rows.append(row)
    return rows


def _parse_date(text: str) -> tuple[date | None, bool]:
    """(parsed_date_or_None, had_error). Blank is a valid "no date", not an
    error — only a non-blank value that fails to parse as DD/MM/YYYY is
    flagged."""
    if not text:
        return None, False
    try:
        return datetime.strptime(text, _DATE_FORMAT).date(), False
    except ValueError:
        return None, True


def _find_client(name: str, clients: list) -> dict | None:
    if not name:
        return None
    lname = name.lower()
    for c in clients:
        if c["name"].lower() == lname:
            return c
    return None


def build_preview(rows: list[dict], clients: list) -> list[dict]:
    """One entry per CSV row: the raw fields, the matched client (or None —
    the view flags this for a manual pick, it's never auto-created), the
    parsed dates, and whether the document type is present at all (a row
    without one can't be saved, since it's the one required field)."""
    preview = []
    for i, row in enumerate(rows):
        client = _find_client(row[COL_CLIENT], clients)
        issue_date, issue_error = _parse_date(row[COL_ISSUE])
        expiry_date, expiry_error = _parse_date(row[COL_EXPIRY])
        preview.append({
            "index": i,
            "raw": row,
            "client": client,
            "issue_date": issue_date,
            "issue_date_error": issue_error,
            "expiry_date": expiry_date,
            "expiry_date_error": expiry_error,
            "missing_doctype": not row[COL_DOCTYPE],
        })
    return preview


def commit_import(resolved_rows: list[dict], actor_id: int) -> dict:
    """resolved_rows: each {"client_id", "document_type", "position",
    "subject_name", "issue_date", "expiry_date"} — the view's final values
    per row after any manual fix-up. Rows with no client or no document
    type are the caller's job to exclude before calling this; skipped here
    too as a last-resort safety net. One compliance_item per remaining row."""
    from core import models

    created = 0
    skipped = 0
    for row in resolved_rows:
        if not row.get("client_id") or not (row.get("document_type") or "").strip():
            skipped += 1
            continue
        models.create_compliance_item(
            row["client_id"],
            row["document_type"],
            position=row.get("position"),
            subject_name=row.get("subject_name"),
            issue_date=row.get("issue_date"),
            expiry_date=row.get("expiry_date"),
            created_by=actor_id,
        )
        created += 1
    return {"items_created": created, "items_skipped": skipped}
