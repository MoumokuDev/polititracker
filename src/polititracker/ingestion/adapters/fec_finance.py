"""Campaign-finance summaries from the openFEC API.

Committee-level per-cycle totals ONLY. Legal constraint (52 U.S.C.
30111(a)(4)): individual contributor data must never be used for solicitation
or commercial purposes — this adapter deliberately ingests no itemized
receipts, and any future change to that must be legally re-reviewed.

Candidate IDs come from the congress-legislators crosswalk
(external_id.id_type == 'fec'; a figure may have several — House and Senate
candidacies are separate).
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import RateLimitedClient, data_gov_client
from polititracker.models import ExternalId, FinanceSource, FinanceSummary

log = logging.getLogger(__name__)

ADAPTER = "fec_finance"
BASE = "https://api.open.fec.gov/v1"
# cycles for which top-employer aggregates are ingested (most recent two)
EMPLOYER_CYCLES = (2026, 2024)
EMPLOYER_TOP_N = 10


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _ingest_employer_sources(
    session: Session, client: RateLimitedClient, candidate_id: str, figure_id: int
) -> None:
    """Top Schedule A sources by contributor-reported employer, per FEC aggregation.

    Aggregates only; individual contributor records are never requested.
    """
    committees = client.get(
        f"{BASE}/candidate/{candidate_id}/committees/", designation="P", per_page=10
    ).json()
    for committee in committees.get("results", []):
        committee_id = committee.get("committee_id")
        if not committee_id:
            continue
        cycles = set(committee.get("cycles") or [])
        for cycle in EMPLOYER_CYCLES:
            if cycle not in cycles:
                continue
            public_url = (
                "https://www.fec.gov/data/receipts/"
                f"?committee_id={committee_id}&two_year_transaction_period={cycle}"
            )
            data = client.get(
                f"{BASE}/schedules/schedule_a/by_employer/",
                committee_id=committee_id,
                cycle=cycle,
                sort="-total",
                per_page=EMPLOYER_TOP_N,
            ).json()
            rows = data.get("results", [])
            if not rows:
                continue
            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=f"{committee_id}/{cycle}/by_employer",
                source_url=public_url,
                payload=data,
            )
            for row in rows:
                name = (row.get("employer") or "NULL")[:256]
                source = session.scalar(
                    select(FinanceSource).where(
                        FinanceSource.figure_id == figure_id,
                        FinanceSource.fec_committee_id == committee_id,
                        FinanceSource.cycle == cycle,
                        FinanceSource.source_type == "employer",
                        FinanceSource.name == name,
                    )
                )
                if source is None:
                    source = FinanceSource(
                        figure_id=figure_id,
                        fec_committee_id=committee_id,
                        cycle=cycle,
                        source_type="employer",
                        name=name,
                    )
                    session.add(source)
                source.total = row.get("total")
                source.contribution_count = row.get("count")
                source.source_url = public_url
                source.source_fetch_id = fetch.id
                source.fetched_at = datetime.now(UTC)


def run(session: Session) -> dict:
    client = data_gov_client(min_interval=3.6)  # FEC budget: 1,000/hr
    with ingestion_run(session, ADAPTER) as run_row:
        candidate_ids = session.execute(
            select(ExternalId.id_value, ExternalId.figure_id)
            .where(ExternalId.id_type == "fec")
            .order_by(ExternalId.id_value)
        ).all()
        run_row.records_seen = len(candidate_ids)

        upserted = 0
        for candidate_id, figure_id in candidate_ids:
            public_url = f"https://www.fec.gov/data/candidate/{candidate_id}/"
            data = client.get(
                f"{BASE}/candidate/{candidate_id}/totals/",
                election_full="false",
                sort="-cycle",
                per_page=20,
            ).json()
            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=candidate_id,
                source_url=public_url,
                payload=data,
            )

            for row in data.get("results", []):
                cycle = row.get("cycle")
                if cycle is None:
                    continue
                summary = session.scalar(
                    select(FinanceSummary).where(
                        FinanceSummary.figure_id == figure_id,
                        FinanceSummary.fec_candidate_id == candidate_id,
                        FinanceSummary.cycle == cycle,
                    )
                )
                if summary is None:
                    summary = FinanceSummary(
                        figure_id=figure_id, fec_candidate_id=candidate_id, cycle=cycle
                    )
                    session.add(summary)
                summary.total_receipts = row.get("receipts")
                summary.total_disbursements = row.get("disbursements")
                summary.cash_on_hand = row.get("last_cash_on_hand_end_period")
                summary.debts = row.get("last_debts_owed_by_committee")
                summary.individual_itemized = row.get("individual_itemized_contributions")
                summary.individual_unitemized = row.get("individual_unitemized_contributions")
                summary.pac_contributions = row.get("other_political_committee_contributions")
                summary.party_contributions = row.get("political_party_committee_contributions")
                summary.candidate_self = row.get("candidate_contribution")
                summary.coverage_end_date = _as_date(row.get("coverage_end_date"))
                summary.source_url = public_url
                summary.source_fetch_id = fetch.id
                summary.fetched_at = datetime.now(UTC)
                upserted += 1

            _ingest_employer_sources(session, client, candidate_id, figure_id)
            session.commit()

        run_row.records_upserted = upserted
        summary_out = {
            "adapter": ADAPTER,
            "candidate_ids": len(candidate_ids),
            "cycle_summaries_upserted": upserted,
        }
        log.info("%s", summary_out)
        return summary_out
