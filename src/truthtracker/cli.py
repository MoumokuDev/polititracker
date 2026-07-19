"""Ingestion CLI.

    python -m truthtracker.cli seed-figures
    python -m truthtracker.cli ingest-crec [--start YYYY-MM-DD] [--end YYYY-MM-DD]
    python -m truthtracker.cli ingest-house-votes [--congress N] [--session N] [--limit N]
    python -m truthtracker.cli ingest-senate-votes [--congress N] [--session N] [--limit N]
    python -m truthtracker.cli enrich-bills [--limit N]
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import date, timedelta

from truthtracker.config import get_settings
from truthtracker.db import get_session
from truthtracker.ingestion.adapters import (
    congress_gov_bills,
    congress_legislators,
    govinfo_crec,
    house_votes,
    manual_seed,
    senate_votes,
)


def _run(fn: Callable) -> int:
    session = get_session()
    try:
        result = fn(session)
    finally:
        session.close()
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(prog="truthtracker")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed-figures", help="Seed TX delegation + executive/judicial figures")

    p = sub.add_parser("ingest-crec", help="Ingest Congressional Record statements")
    p.add_argument("--start", type=date.fromisoformat, default=date.today() - timedelta(days=3))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())

    for name, help_text in (
        ("ingest-house-votes", "Ingest House roll-call votes (Congress.gov)"),
        ("ingest-senate-votes", "Ingest Senate roll-call votes (senate.gov XML)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--congress", type=int, default=settings.current_congress)
        p.add_argument("--session", type=int, default=settings.current_session)
        p.add_argument("--limit", type=int, default=None)

    p = sub.add_parser("enrich-bills", help="Fill bill titles/policy areas from Congress.gov")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("index-statements", help="Chunk + embed statements into pgvector")
    p.add_argument("--embed-limit", type=int, default=None)

    sub.add_parser("fetch-portraits", help="Download official portraits (with provenance)")

    sub.add_parser("ingest-finance", help="FEC per-cycle finance summaries (committee totals)")

    p = sub.add_parser("ingest-scotus", help="SCOTUS opinions via CourtListener (rate-budgeted)")
    p.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(days=30))
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("ingest-fedreg", help="Presidential documents via Federal Register")
    p.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(days=14))
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser(
        "ingest-disclosures", help="House financial-disclosure filing index (Clerk)"
    )
    p.add_argument(
        "--years", type=int, nargs="+", default=[date.today().year, date.today().year - 1]
    )

    p = sub.add_parser("tag-topics", help="Recompute machine topic tags for statements")
    p.add_argument("--threshold", type=float, default=None)

    sub.add_parser("ingest-committees", help="Committee assignments (congress-legislators)")

    p = sub.add_parser(
        "ingest-sponsorship", help="Sponsored/cosponsored legislation per member"
    )
    p.add_argument("--limit-members", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command == "seed-figures":
        return _run(lambda s: [congress_legislators.run(s), manual_seed.run(s)])
    if args.command == "ingest-crec":
        return _run(lambda s: govinfo_crec.run(s, args.start, args.end))
    if args.command == "ingest-house-votes":
        return _run(lambda s: house_votes.run(s, args.congress, args.session, args.limit))
    if args.command == "ingest-senate-votes":
        return _run(lambda s: senate_votes.run(s, args.congress, args.session, args.limit))
    if args.command == "enrich-bills":
        return _run(lambda s: congress_gov_bills.run(s, args.limit))
    if args.command == "index-statements":
        from truthtracker.search import indexer

        return _run(lambda s: indexer.run(s, args.embed_limit))
    if args.command == "fetch-portraits":
        from truthtracker.ingestion.adapters import portraits

        return _run(portraits.run)
    if args.command == "ingest-finance":
        from truthtracker.ingestion.adapters import fec_finance

        return _run(fec_finance.run)
    if args.command == "ingest-scotus":
        from truthtracker.ingestion.adapters import courtlistener_scotus

        return _run(lambda s: courtlistener_scotus.run(s, args.since, args.limit))
    if args.command == "ingest-fedreg":
        from truthtracker.ingestion.adapters import federal_register

        return _run(lambda s: federal_register.run(s, args.since, args.limit))
    if args.command == "ingest-disclosures":
        from truthtracker.ingestion.adapters import house_disclosures

        return _run(lambda s: house_disclosures.run(s, args.years))
    if args.command == "tag-topics":
        from truthtracker.search import topics

        return _run(lambda s: topics.run(s, args.threshold))
    if args.command == "ingest-committees":
        from truthtracker.ingestion.adapters import committees

        return _run(committees.run)
    if args.command == "ingest-sponsorship":
        from truthtracker.ingestion.adapters import member_sponsorship

        return _run(lambda s: member_sponsorship.run(s, args.limit_members))
    return 2


if __name__ == "__main__":
    sys.exit(main())
