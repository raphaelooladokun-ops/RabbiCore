"""Manual/CI entrypoint: apply the schema and seed reference + demo data.

The app also does this automatically on first load (see core/bootstrap.py),
so you normally don't need to run this by hand — it's here for CI or a
one-off manual setup.

Usage:
    python scripts/init_db.py

Reads the Neon connection string the same way the app does: from
.streamlit/secrets.toml (see .streamlit/secrets.toml.example).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.bootstrap import ensure_schema, seed_catalogue, seed_demo_data  # noqa: E402

if __name__ == "__main__":
    ensure_schema()
    print("Schema applied.")
    seed_catalogue()
    print("Service catalogue seeded (43 services).")
    seed_demo_data()
    print("Demo clients, staff logins, and sample jobs seeded.")
