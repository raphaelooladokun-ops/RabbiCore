-- Rabbi Core — shared spine schema
-- Every future module (immigration, CIT, CAC, state matters) is built on these
-- four tables + the service catalogue. Category-specific detail never lives
-- here — it attaches via job_extension. Safe to re-run: everything is
-- CREATE ... IF NOT EXISTS / CREATE OR REPLACE.

-- ---------------------------------------------------------------------------
-- CLIENT
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    rc_number       TEXT,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- STAFF — every human who logs in
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staff (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('principal', 'admin', 'specialist', 'client')),
    client_id       INTEGER REFERENCES client(id) ON DELETE SET NULL,  -- set when role = 'client'
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- SERVICE CATALOGUE — the locked 43-service catalogue (CAC / Immigration /
-- CIT / State). `fields` describes the Type/Routing/Location/Scheme
-- sub-fields the capture screen must render when this service is picked.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_catalogue (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    pillar          TEXT NOT NULL CHECK (pillar IN ('CAC', 'Immigration', 'CIT', 'State')),
    name            TEXT NOT NULL,
    fields          JSONB NOT NULL DEFAULT '[]'::jsonb,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- INVOICE — one invoice groups many jobs. Admin creates (submits for
-- approval); principal approves. A job can only close once its invoice is
-- approved (or paid) — see the status-guard trigger below.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice (
    id              SERIAL PRIMARY KEY,
    invoice_code    TEXT NOT NULL UNIQUE,
    client_id       INTEGER NOT NULL REFERENCES client(id),
    status          TEXT NOT NULL DEFAULT 'pending_approval'
                        CHECK (status IN ('pending_approval', 'approved', 'paid')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_at       TIMESTAMPTZ,
    paid_at         TIMESTAMPTZ
);

ALTER TABLE invoice ADD COLUMN IF NOT EXISTS created_by INTEGER REFERENCES staff(id);
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS approved_by INTEGER REFERENCES staff(id);
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS invoice_date DATE NOT NULL DEFAULT CURRENT_DATE;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS rejected_by INTEGER REFERENCES staff(id);
ALTER TABLE invoice ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ;

-- Migration for installs created before the admin-creates / principal-approves
-- flow: map the old draft/issued statuses onto the new ones, and widen to
-- allow 'rejected' (principal sends an invoice back to admin with a reason
-- instead of restructuring it themselves). Drop the old constraint FIRST so
-- the old values are still legal while we remap them. No-op once migrated.
ALTER TABLE invoice DROP CONSTRAINT IF EXISTS invoice_status_check;
UPDATE invoice SET status = 'pending_approval' WHERE status = 'draft';
UPDATE invoice SET status = 'approved' WHERE status = 'issued';
ALTER TABLE invoice ADD CONSTRAINT invoice_status_check
    CHECK (status IN ('pending_approval', 'approved', 'paid', 'rejected'));
ALTER TABLE invoice ALTER COLUMN status SET DEFAULT 'pending_approval';

-- ---------------------------------------------------------------------------
-- JOB — the central object of the whole suite
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job (
    id                  SERIAL PRIMARY KEY,
    job_id              TEXT NOT NULL UNIQUE,          -- human-readable, e.g. JOB-2026-0014
    client_id           INTEGER REFERENCES client(id),  -- nullable: a dismissed item may not be tied to a client
    category            TEXT NOT NULL CHECK (category IN ('front_office', 'cac', 'immigration', 'cit', 'state')),
    service_type        TEXT REFERENCES service_catalogue(code),  -- null only for a dismissed, non-catalogue item
    title               TEXT NOT NULL,
    description         TEXT,
    owner_id            INTEGER REFERENCES staff(id),
    status              TEXT NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'in_progress', 'blocked', 'done', 'closed', 'dismissed')),
    source              TEXT NOT NULL CHECK (source IN ('client_email', 'team_group_forward')),
    created_by          INTEGER REFERENCES staff(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sla_date            DATE,
    blocked_by          INTEGER REFERENCES job(id),
    invoice_id          INTEGER REFERENCES invoice(id),
    waiting_on_client    TEXT,          -- plain-language note: what we need from the client, and by when
    internal_notes       TEXT,          -- staff-only, never shown on the client view
    dismissed_reason      TEXT,

    -- A job cannot be closed without being invoiced first (belt-and-braces:
    -- also re-checked in the status-guard trigger below on every write path).
    CONSTRAINT job_closed_requires_invoice CHECK (status <> 'closed' OR invoice_id IS NOT NULL)
);

-- Widening migration for installs created before client_id became nullable
-- (dismissed items may not be tied to a client); no-op if already nullable.
ALTER TABLE job ALTER COLUMN client_id DROP NOT NULL;

-- The reason captured when a job is marked done or blocked (required by the
-- UI for those two transitions) — holds the reason for the CURRENT status,
-- overwritten on the next status change. Not a full history log, by design.
ALTER TABLE job ADD COLUMN IF NOT EXISTS status_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_job_client_id ON job(client_id);
CREATE INDEX IF NOT EXISTS idx_job_owner_id ON job(owner_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);
CREATE INDEX IF NOT EXISTS idx_job_category ON job(category);
CREATE INDEX IF NOT EXISTS idx_job_invoice_id ON job(invoice_id);
CREATE INDEX IF NOT EXISTS idx_job_blocked_by ON job(blocked_by);

-- ---------------------------------------------------------------------------
-- JOB EXTENSION — category-specific fields attach here, never on job itself.
-- One row per job holding the Type/Routing/Location/Scheme values chosen
-- for that job's service. Future specialist modules add their own detail
-- tables the same way, or reuse this one — the core table never changes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_extension (
    job_id          INTEGER PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- JOB EXPENSE — a simple running list of costs tied to a job. Admin adds
-- them; principal can see them. Not accounting — just a log.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_expense (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    expense_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    created_by      INTEGER REFERENCES staff(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_expense_job_id ON job_expense(job_id);

-- ---------------------------------------------------------------------------
-- JOB COMMENT — a lightweight comment thread on a job. Anyone with access to
-- the job can post; kept simple on purpose (no edits, no threading).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_comment (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    author_id       INTEGER NOT NULL REFERENCES staff(id),
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_comment_job_id ON job_comment(job_id);

-- ---------------------------------------------------------------------------
-- INVOICE LINE — a real invoice's line items. One line per job on the
-- invoice: description + amount, editable by the principal at review time,
-- but the set of jobs (which rows exist here) is admin-only to change —
-- that's what makes "which jobs are on this invoice" an accountable,
-- admin-owned decision rather than something the approver can quietly edit.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice_line (
    id              SERIAL PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    job_id          INTEGER NOT NULL REFERENCES job(id),
    description     TEXT NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount >= 0)
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_invoice_id ON invoice_line(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_line_job_id ON invoice_line(job_id);

-- ---------------------------------------------------------------------------
-- NOTIFICATION — so each role is alerted rather than having to discover
-- things. link_type/link_id point at a job or an invoice (no single FK is
-- possible across two target tables); resolved in the application layer.
-- `kind` lets the SLA sweep dedupe against itself without suppressing other
-- notification kinds for the same job.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification (
    id              SERIAL PRIMARY KEY,
    staff_id        INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    link_type       TEXT NOT NULL CHECK (link_type IN ('job', 'invoice')),
    link_id         INTEGER NOT NULL,
    message         TEXT NOT NULL,
    read            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notification_staff_id ON notification(staff_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_unread ON notification(staff_id) WHERE NOT read;
CREATE INDEX IF NOT EXISTS idx_notification_dedup ON notification(staff_id, kind, link_type, link_id);

-- ---------------------------------------------------------------------------
-- STATUS GUARD — the rules the brief says the system must enforce, kept in
-- the database so no future module or UI can bypass them:
--   1. A job cannot become 'closed' without an invoice, and that invoice
--      must be approved (or paid) — not just attached.
--   2. A job with an unresolved blocked_by is forced to show 'blocked' and
--      cannot advance to in_progress / done / closed.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION job_status_guard() RETURNS trigger AS $$
DECLARE
    blocker_status TEXT;
    invoice_status_val TEXT;
BEGIN
    IF NEW.blocked_by IS NOT NULL AND NEW.status <> 'dismissed' THEN
        SELECT status INTO blocker_status FROM job WHERE id = NEW.blocked_by;

        IF blocker_status IS NOT NULL AND blocker_status NOT IN ('done', 'closed') THEN
            IF NEW.status IN ('in_progress', 'done', 'closed') THEN
                RAISE EXCEPTION 'Job % is blocked by unresolved job % and cannot move to %',
                    NEW.job_id, NEW.blocked_by, NEW.status;
            END IF;
            NEW.status := 'blocked';
        END IF;
    END IF;

    IF NEW.status = 'closed' THEN
        IF NEW.invoice_id IS NULL THEN
            RAISE EXCEPTION 'Job % cannot be closed without an invoice', NEW.job_id;
        END IF;

        SELECT status INTO invoice_status_val FROM invoice WHERE id = NEW.invoice_id;
        IF invoice_status_val NOT IN ('approved', 'paid') THEN
            RAISE EXCEPTION 'Job % cannot be closed until its invoice is approved', NEW.job_id;
        END IF;
    END IF;

    NEW.updated_at := now();

    IF TG_OP = 'INSERT' THEN
        NEW.status_changed_at := now();
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_changed_at := now();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_job_status_guard ON job;
CREATE TRIGGER trg_job_status_guard
    BEFORE INSERT OR UPDATE ON job
    FOR EACH ROW EXECUTE FUNCTION job_status_guard();
