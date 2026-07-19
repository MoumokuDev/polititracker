"""One-shot backfill of the 119th Congress (2025-01-03 → today).

Safe to re-run: every adapter skips already-ingested units, so interruption
costs nothing but time. Progress is visible in the ingestion_run and
source_package tables (and /healthz), and in this script's stdout (redirect
to backfill.log when launching detached).

    python scripts/backfill.py
"""

import logging
import sys
import time
from datetime import date

from polititracker.db import get_session
from polititracker.ingestion.adapters import (
    congress_gov_bills,
    govinfo_crec,
    house_votes,
    senate_votes,
)
from polititracker.search import indexer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("backfill")

CONGRESS = 119
CREC_START = date(2025, 1, 3)  # 119th Congress convened

STEPS = [
    ("senate votes, session 1", lambda s: senate_votes.run(s, CONGRESS, 1)),
    ("senate votes, session 2", lambda s: senate_votes.run(s, CONGRESS, 2)),
    ("house votes, session 1", lambda s: house_votes.run(s, CONGRESS, 1)),
    ("house votes, session 2", lambda s: house_votes.run(s, CONGRESS, 2)),
    ("congressional record", lambda s: govinfo_crec.run(s, CREC_START, date.today())),
    ("bill enrichment", lambda s: congress_gov_bills.run(s, limit=5000)),
    ("chunk + embed statements", lambda s: indexer.run(s)),
]


def main() -> int:
    failures = 0
    for name, step in STEPS:
        started = time.monotonic()
        log.info("=== step: %s ===", name)
        session = get_session()
        try:
            summary = step(session)
            log.info("=== step done (%.0fs): %s", time.monotonic() - started, summary)
        except Exception:
            failures += 1
            log.exception("=== step FAILED: %s (continuing; re-run to resume)", name)
        finally:
            session.close()
    log.info("backfill complete; failed steps: %d", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
