"""One-time cleanup: folds every still-open, per-client job for a recurring
service (Monthly VAT Returns, PAYE & WHT, Payroll, Pension, Annual Return,
...) onto its period's shared checklist job, then hides the old per-client
job. Safe to run more than once — a job it already hid is never picked up
again, and a client already on a period's checklist is left untouched.

Usage:
    python scripts/migrate_recurring_jobs.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import models  # noqa: E402

if __name__ == "__main__":
    actor = models.query_one("SELECT id FROM staff WHERE role = 'super_admin' ORDER BY id LIMIT 1")
    if not actor:
        print("No super_admin account found — nothing to run as.")
        sys.exit(1)

    result = models.migrate_current_period_recurring_jobs(actor["id"])
    print(
        f"Folded {result['jobs_migrated']} per-client job(s) into "
        f"{result['parents_touched']} shared checklist job(s) "
        f"({result['jobs_skipped']} skipped for missing a due date)."
    )
