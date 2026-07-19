"""Ingestion worker: scheduled adapter runs.

Runs as its own container in docker compose. Every adapter run writes an
ingestion_run row (success or failure) — the health view reads that ledger,
so a silent worker is impossible by construction. Job times are UTC; CREC
issues publish early morning US-Eastern, hence the midday-UTC schedule.
"""

import logging
from datetime import date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from truthtracker.config import get_settings
from truthtracker.db import get_session
from truthtracker.ingestion.adapters import (
    congress_gov_bills,
    govinfo_crec,
    house_votes,
    senate_votes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("truthtracker.worker")


def _job(fn) -> None:
    session = get_session()
    try:
        fn(session)
    except Exception:
        # ingestion_run already recorded the failure; keep the scheduler alive.
        log.exception("scheduled ingestion failed")
    finally:
        session.close()


def crec_daily() -> None:
    _job(lambda s: govinfo_crec.run(s, date.today() - timedelta(days=3), date.today()))


def votes_daily() -> None:
    settings = get_settings()
    _job(lambda s: house_votes.run(s, settings.current_congress, settings.current_session))
    _job(lambda s: senate_votes.run(s, settings.current_congress, settings.current_session))


def bills_daily() -> None:
    _job(lambda s: congress_gov_bills.run(s, limit=200))


def index_daily() -> None:
    from truthtracker.search import indexer  # heavy import (torch), deferred

    _job(lambda s: indexer.run(s))


def topics_daily() -> None:
    from truthtracker.search import topics  # heavy import (torch), deferred

    _job(topics.run)


def portraits_weekly() -> None:
    from truthtracker.ingestion.adapters import portraits

    _job(portraits.run)


def finance_weekly() -> None:
    from truthtracker.ingestion.adapters import fec_finance

    _job(fec_finance.run)


def scotus_daily() -> None:
    from truthtracker.ingestion.adapters import courtlistener_scotus

    _job(lambda s: courtlistener_scotus.run(s, date.today() - timedelta(days=14), limit=10))


def fedreg_daily() -> None:
    from truthtracker.ingestion.adapters import federal_register

    _job(lambda s: federal_register.run(s, date.today() - timedelta(days=7)))


def disclosures_weekly() -> None:
    from truthtracker.ingestion.adapters import house_disclosures

    _job(lambda s: house_disclosures.run(s, [date.today().year, date.today().year - 1]))


def main() -> None:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(crec_daily, "cron", hour=12, minute=15)
    scheduler.add_job(votes_daily, "cron", hour=12, minute=45)
    scheduler.add_job(bills_daily, "cron", hour=13, minute=15)
    scheduler.add_job(index_daily, "cron", hour=13, minute=45)
    scheduler.add_job(topics_daily, "cron", hour=14, minute=5)
    scheduler.add_job(portraits_weekly, "cron", day_of_week="mon", hour=14, minute=15)
    scheduler.add_job(finance_weekly, "cron", day_of_week="sun", hour=14, minute=0)
    scheduler.add_job(scotus_daily, "cron", hour=14, minute=30)
    scheduler.add_job(fedreg_daily, "cron", hour=14, minute=45)
    scheduler.add_job(disclosures_weekly, "cron", day_of_week="sat", hour=14, minute=0)
    log.info(
        "worker scheduler starting (crec 12:15Z, votes 12:45Z, bills 13:15Z, "
        "index 13:45Z, scotus 14:30Z, fedreg 14:45Z, portraits Mon 14:15Z, "
        "finance Sun 14:00Z)"
    )
    scheduler.start()


if __name__ == "__main__":
    main()
