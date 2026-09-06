"""Seed data for the document checklist mechanism (`document_type` +
`service_document_requirement`). Source: Rabbi Documents Processed —
STEP 1 (per-service document lists) and STEP 2 (the master document-type
table with expiry flags). Immigration is the first module to populate this;
CIT/State add their own document types and requirements here the same way.

Everything here is idempotent (ON CONFLICT ... DO UPDATE / DO NOTHING) —
safe to re-run on every app startup, same as seed_data.py.
"""

# ---------------------------------------------------------------------------
# DOCUMENT TYPES — has_expiry marks the ones the source table flags "Y":
# permits, certificates and cards that lapse and need tracking. Everything
# else is a one-time or point-in-time document with no expiry to watch.
# ---------------------------------------------------------------------------
DOCUMENT_TYPES = [
    {"code": "APP-LETTER", "name": "Application Letter", "has_expiry": False},
    {"code": "TCC-COMPANY", "name": "Tax Clearance Certificate (Company)", "has_expiry": True},
    {"code": "CCI", "name": "Certificate of Capital Importation", "has_expiry": False},
    {"code": "BANK-REF", "name": "Bank Reference", "has_expiry": False},
    {"code": "MEMART", "name": "Memorandum & Articles of Association", "has_expiry": False},
    {"code": "CAC-STATUS-REPORT", "name": "CAC Status Report", "has_expiry": False},
    {"code": "CERT-INCORPORATION", "name": "Certificate of Incorporation", "has_expiry": False},
    {"code": "BUSINESS-PLAN", "name": "Company Profile / Business Plan / Feasibility Report", "has_expiry": False},
    {"code": "LEASE-AGREEMENT", "name": "Lease / Tenancy Agreement / C-of-O", "has_expiry": True},
    {"code": "JV-AGREEMENT", "name": "Joint Venture Agreement", "has_expiry": False},
    {"code": "TRAINING-PROGRAMME", "name": "Training Programme (Nigerian understudies)", "has_expiry": False},
    {"code": "EQUIP-IMPORT-EVIDENCE", "name": "Evidence of Equipment Importation", "has_expiry": False},
    {"code": "LICENCE-PERMIT", "name": "Licence / Permit (if applicable)", "has_expiry": False},
    {"code": "BUSINESS-EXPANSION-EVIDENCE", "name": "Evidence of Business Expansion", "has_expiry": False},
    {"code": "JOB-DESC-QUOTA", "name": "Job Description (quota position)", "has_expiry": False},
    {"code": "INDIGENISATION-EVIDENCE", "name": "Evidence of Indigenisation", "has_expiry": False},
    {"code": "AUDITED-FS", "name": "Audited Financial Statements", "has_expiry": False},
    {"code": "CAC-ACK-LETTER", "name": "CAC Acknowledgement Letter", "has_expiry": False},
    {"code": "EXPAT-TCC", "name": "Tax Clearance Certificate — expatriate", "has_expiry": True},
    {"code": "EXPAT-QUOTA-RETURNS", "name": "Expatriate Quota Returns (last 3 months)", "has_expiry": False},
    {"code": "NIGERIAN-UNDERSTUDIES-LIST", "name": "List of Nigerian Understudies", "has_expiry": False},
    {"code": "NIGERIAN-SENIOR-STAFF-LIST", "name": "List of Nigerian Senior/Management Staff", "has_expiry": False},
    {"code": "ITF-COMPLIANCE-CERT", "name": "ITF Compliance Certificate", "has_expiry": True},
    # Gate-critical: the current quota approval's validity is read straight
    # off this document type's expiry_date on the linked quota job.
    {"code": "QUOTA-APPROVAL", "name": "Copy of Quota Approval", "has_expiry": True},
    {"code": "BUSINESS-PERMIT", "name": "Business Permit", "has_expiry": True},
    {"code": "INVITATION-LETTER", "name": "Invitation Letter", "has_expiry": False},
    {"code": "PASSPORT-DATA-PAGE", "name": "Passport Data Page", "has_expiry": True},
    {"code": "AIR-TICKET", "name": "Air Ticket", "has_expiry": False},
    {"code": "SPONSOR-CERPAC", "name": "Sponsor's CERPAC Card", "has_expiry": True},
    {"code": "CV", "name": "CV", "has_expiry": False},
    {"code": "ACADEMIC-CERT", "name": "Academic / Professional Certificate", "has_expiry": False},
    {"code": "OLD-CERPAC-CARD", "name": "Old CERPAC Card", "has_expiry": True},
    {"code": "APPROVED-QUOTA-POSITIONS", "name": "Approved Quota Positions", "has_expiry": False},
    {"code": "APPOINTMENT-LETTER", "name": "Appointment Letter", "has_expiry": False},
    {"code": "ACCEPTANCE-LETTER", "name": "Acceptance Letter", "has_expiry": False},
    {"code": "IMM-RESPONSIBILITY-LETTER", "name": "Immigration Responsibility Letter", "has_expiry": False},
    {"code": "BOARD-RESOLUTION", "name": "Board Resolution (MD positions)", "has_expiry": False},
    {"code": "POLICE-CERT", "name": "Police Certificate", "has_expiry": True},
    {"code": "STAMP-PAGE", "name": "Passport Stamp / Endorsement Page", "has_expiry": False},
    {"code": "CERPAC-CARD", "name": "CERPAC Card", "has_expiry": True},
    {"code": "EMPLOYER-SUPPORT-LETTER", "name": "Employer Support Letter", "has_expiry": False},
    {"code": "CONSENT-LETTER-PRINCIPAL", "name": "Letter of Consent of Principal Expatriate", "has_expiry": False},
    {"code": "MARRIAGE-CERT", "name": "Marriage Certificate", "has_expiry": False},
    {"code": "BIRTH-CERT", "name": "Birth Certificate", "has_expiry": False},
    {"code": "DELETION-LETTER", "name": "Deletion Letter", "has_expiry": False},
    {"code": "RESIGNATION-LETTER", "name": "Resignation Letter", "has_expiry": False},
    {"code": "NOC", "name": "NOC (No Objection Certificate)", "has_expiry": False},
]


def _all(service_code: str, codes: list) -> list:
    """Requirement rows that apply to every variant of this service (or the
    service has no Type field at all)."""
    return [{"service_code": service_code, "variant": "*", "document_type_code": c} for c in codes]


def _variant(service_code: str, variant: str, codes: list) -> list:
    """Requirement rows scoped to one Type-field value of this service, on
    top of whatever that service's '*' rows already require."""
    return [{"service_code": service_code, "variant": variant, "document_type_code": c} for c in codes]


# ---------------------------------------------------------------------------
# SERVICE DOCUMENT REQUIREMENTS — per Immigration service (and, where the
# catalogue gives it a Type field, per variant). IMM-NIS-INSPECTION has no
# rows on purpose — the source data is explicit that it has no fixed
# checklist (what NIS asks for on the day).
# ---------------------------------------------------------------------------
SERVICE_DOCUMENT_REQUIREMENTS = (
    _all("IMM-BUSINESS-PERMIT", [
        "APP-LETTER", "TCC-COMPANY", "CCI", "BANK-REF", "MEMART", "CAC-STATUS-REPORT",
        "CERT-INCORPORATION", "BUSINESS-PLAN", "LEASE-AGREEMENT",
    ])
    + _variant("IMM-QUOTA", "Grant", [
        "APP-LETTER", "CERT-INCORPORATION", "MEMART", "BUSINESS-PLAN", "CAC-STATUS-REPORT",
        "JV-AGREEMENT", "TCC-COMPANY", "LEASE-AGREEMENT", "TRAINING-PROGRAMME", "CCI",
        "BUSINESS-PERMIT", "QUOTA-APPROVAL",
    ])
    + _variant("IMM-QUOTA", "Addition", [
        "APP-LETTER", "TCC-COMPANY", "TRAINING-PROGRAMME", "CCI", "EQUIP-IMPORT-EVIDENCE",
        "LICENCE-PERMIT", "BUSINESS-EXPANSION-EVIDENCE", "JOB-DESC-QUOTA",
        "INDIGENISATION-EVIDENCE", "AUDITED-FS", "CAC-ACK-LETTER", "QUOTA-APPROVAL",
    ])
    + _variant("IMM-QUOTA", "Renewal", [
        "APP-LETTER", "TCC-COMPANY", "EXPAT-TCC", "EXPAT-QUOTA-RETURNS", "TRAINING-PROGRAMME",
        "NIGERIAN-UNDERSTUDIES-LIST", "NIGERIAN-SENIOR-STAFF-LIST", "AUDITED-FS",
        "CAC-ACK-LETTER", "ITF-COMPLIANCE-CERT", "QUOTA-APPROVAL", "BUSINESS-PERMIT",
    ])
    + _all("IMM-VISA-SHORT-STAY", [
        "INVITATION-LETTER", "PASSPORT-DATA-PAGE", "CERT-INCORPORATION", "CAC-STATUS-REPORT",
        "AIR-TICKET", "SPONSOR-CERPAC",
    ])
    + _all("IMM-VISA-TWP", [
        "PASSPORT-DATA-PAGE", "CV", "ACADEMIC-CERT", "APP-LETTER", "AIR-TICKET",
    ])
    + _all("IMM-ECERPAC-PRINCIPAL", [
        "PASSPORT-DATA-PAGE", "CV", "ACADEMIC-CERT", "APPROVED-QUOTA-POSITIONS",
        "APPOINTMENT-LETTER", "ACCEPTANCE-LETTER", "IMM-RESPONSIBILITY-LETTER",
        "BOARD-RESOLUTION", "POLICE-CERT",
    ])
    + _variant("IMM-ECERPAC-PRINCIPAL", "Renewal", ["OLD-CERPAC-CARD"])
    + _variant("IMM-ECERPAC-PRINCIPAL", "Regularization", ["STAMP-PAGE"])
    + _all("IMM-ECERPAC-DEP-SPOUSE", [
        "PASSPORT-DATA-PAGE", "CERPAC-CARD", "EMPLOYER-SUPPORT-LETTER",
        "CONSENT-LETTER-PRINCIPAL", "MARRIAGE-CERT",
    ])
    + _all("IMM-ECERPAC-DEP-CHILD", [
        "PASSPORT-DATA-PAGE", "STAMP-PAGE", "EMPLOYER-SUPPORT-LETTER",
        "CONSENT-LETTER-PRINCIPAL", "BIRTH-CERT", "APP-LETTER",
    ])
    + _all("IMM-DELETION-EXPAT", [
        "DELETION-LETTER", "AIR-TICKET", "PASSPORT-DATA-PAGE", "CERPAC-CARD", "RESIGNATION-LETTER",
    ])
    + _all("IMM-CHANGE-EXPAT", ["NOC", "RESIGNATION-LETTER"])
)
