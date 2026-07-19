"""Congressional Record statements via the GovInfo API.

Flow per daily issue (package):
  collections listing → source_package row (the corpus coverage map)
  → package MODS (one request; per-granule member attribution)
  → granule htm for granules featuring tracked members
  → speaker turns become statement rows (verbatim substrings, enforced).

Honesty guarantees:
- source_package records exactly which issues the corpus covers, so search
  misses can report their true date bounds.
- crec_floor vs crec_extension is preserved: Extensions of Remarks were
  SUBMITTED to the Record, not spoken — the UI must label them as such.
- Granules with >30 attributed members are vote tallies / cosponsor lists,
  not speeches; skipped.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.crec_parser import (
    extract_turns,
    granule_plain_text,
    parse_mods_granules,
)
from polititracker.ingestion.http import RateLimitedClient, data_gov_client
from polititracker.ingestion.votes_common import bioguide_figure_map
from polititracker.models import SourcePackage, Statement

log = logging.getLogger(__name__)

ADAPTER = "govinfo_crec"
API = "https://api.govinfo.gov"
SOURCE_TYPES = {"HOUSE": "crec_floor", "SENATE": "crec_floor", "EXTENSIONS": "crec_extension"}
MAX_ATTRIBUTED_MEMBERS = 30


def _list_packages(client: RateLimitedClient, start: date, end: date) -> list[dict]:
    """CREC issues with dateIssued in [start, end] inclusive.

    Uses the /published service (dateIssued semantics; /collections filters by
    lastModified). Its end date is exclusive, hence the +1 day.
    """
    packages, mark = [], "*"
    end_exclusive = end + timedelta(days=1)
    while True:
        page = client.get(
            f"{API}/published/{start}/{end_exclusive}",
            collection="CREC",
            pageSize=100,
            offsetMark=mark,
        ).json()
        packages.extend(page.get("packages", []))
        next_url = page.get("nextPage")
        if not next_url or not page.get("packages"):
            return packages
        import urllib.parse as up

        mark = up.parse_qs(up.urlparse(next_url).query).get("offsetMark", [None])[0]
        if mark is None:
            return packages


def _ingest_package(
    session: Session,
    client: RateLimitedClient,
    package_id: str,
    date_issued: date,
    figures: dict[str, int],
) -> int:
    pkg_row = session.scalar(
        select(SourcePackage).where(
            SourcePackage.adapter == ADAPTER, SourcePackage.package_id == package_id
        )
    )
    if pkg_row is None:
        pkg_row = SourcePackage(adapter=ADAPTER, package_id=package_id, issue_date=date_issued)
        session.add(pkg_row)
        session.flush()

    mods_xml = client.get(f"{API}/packages/{package_id}/mods").text
    granules = parse_mods_granules(mods_xml)

    index_fetch = record_fetch(
        session,
        adapter=ADAPTER,
        native_id=f"{package_id}/granule-index",
        source_url=f"https://www.govinfo.gov/app/details/{package_id}",
        payload={
            "granules": [
                {
                    "granule_id": g.granule_id,
                    "class": g.granule_class,
                    "sub_class": g.sub_class,
                    "title": g.title,
                    "members": g.members,
                }
                for g in granules
            ]
        },
    )

    statement_count = 0
    for meta in granules:
        source_type = SOURCE_TYPES.get(meta.granule_class)
        if source_type is None:
            continue
        tracked = [(b, p) for b, p in meta.members if b in figures]
        if not tracked or len(meta.members) > MAX_ATTRIBUTED_MEMBERS:
            continue

        htm = client.get(f"{API}/packages/{package_id}/granules/{meta.granule_id}/htm").text
        text = granule_plain_text(htm)
        granule_fetch = record_fetch(
            session,
            adapter=ADAPTER,
            native_id=meta.granule_id,
            source_url=(
                f"https://www.govinfo.gov/content/pkg/{package_id}/html/{meta.granule_id}.htm"
            ),
            payload={"htm": htm, "text": text},
        )

        occurred = date.fromisoformat(meta.date) if meta.date else date_issued
        for turn in extract_turns(text, tracked):
            native_id = f"{meta.granule_id}:{turn.turn_index}"
            stmt = session.scalar(
                select(Statement).where(
                    Statement.source_type == source_type, Statement.native_id == native_id
                )
            )
            if stmt is None:
                stmt = Statement(
                    source_type=source_type,
                    native_id=native_id,
                    figure_id=figures[turn.bioguide],
                    utterance_text=turn.text,
                    occurred_on=occurred,
                    source_url=(
                        f"https://www.govinfo.gov/app/details/{package_id}/{meta.granule_id}"
                    ),
                    source_fetch_id=granule_fetch.id,
                )
                session.add(stmt)
            stmt.figure_id = figures[turn.bioguide]
            stmt.utterance_text = turn.text
            stmt.occurred_on = occurred
            stmt.heading = meta.title
            stmt.confidence = 0.95
            stmt.attribution_method = "crec_mods_parsed_name"
            stmt.source_fetch_id = granule_fetch.id
            stmt.source_package_id = pkg_row.id
            statement_count += 1

    pkg_row.status = "ingested"
    pkg_row.statement_count = statement_count
    pkg_row.fetched_at = datetime.now(UTC)
    pkg_row.source_fetch_id = index_fetch.id
    return statement_count


def run(session: Session, start: date, end: date) -> dict:
    """Ingest all CREC issues published in [start, end]."""
    client = data_gov_client(min_interval=3.6)  # GovInfo budget: 1,000/hr
    with ingestion_run(session, ADAPTER) as run_row:
        figures = bioguide_figure_map(session)
        packages = _list_packages(client, start, end)
        run_row.records_seen = len(packages)

        total_statements = 0
        ingested = 0
        for pkg in packages:
            package_id = pkg["packageId"]
            date_issued = date.fromisoformat(pkg["dateIssued"])
            existing = session.scalar(
                select(SourcePackage).where(
                    SourcePackage.adapter == ADAPTER,
                    SourcePackage.package_id == package_id,
                )
            )
            if existing is not None and existing.status == "ingested":
                continue
            try:
                total_statements += _ingest_package(
                    session, client, package_id, date_issued, figures
                )
                ingested += 1
                session.commit()
            except Exception:
                session.rollback()
                failed = session.scalar(
                    select(SourcePackage).where(
                        SourcePackage.adapter == ADAPTER,
                        SourcePackage.package_id == package_id,
                    )
                )
                if failed is None:
                    failed = SourcePackage(
                        adapter=ADAPTER, package_id=package_id, issue_date=date_issued
                    )
                    session.add(failed)
                failed.status = "failed"
                session.commit()
                raise  # fail loudly; the package is marked failed for the health view

        run_row.records_upserted = total_statements
        session.commit()
        summary = {
            "adapter": ADAPTER,
            "packages_seen": len(packages),
            "packages_ingested": ingested,
            "statements_upserted": total_statements,
        }
        log.info("%s", summary)
        return summary
