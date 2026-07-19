"""Sponsored and cosponsored legislation per member, via the Congress.gov API.

Fills bill_sponsorship (is_original distinguishes sponsor from cosponsor) and
creates minimal bill rows carrying the title/policy area the listing already
provides; CRS summaries arrive later via the daily enrichment job. Amendment
entries in the listings are skipped.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.config import get_settings
from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import RateLimitedClient, data_gov_client
from polititracker.ingestion.votes_common import BILL_SLUGS, upsert_bill
from polititracker.models import BillSponsorship, Figure

log = logging.getLogger(__name__)

ADAPTER = "member_sponsorship"
BASE = "https://api.congress.gov/v3"
PAGE = 250


def _pages(client: RateLimitedClient, url: str, list_key: str):
    offset = 0
    while True:
        data = client.get(url, format="json", limit=PAGE, offset=offset).json()
        items = data.get(list_key, [])
        yield from items
        offset += PAGE
        if not items or offset >= data.get("pagination", {}).get("count", 0):
            return


def _ingest_member(
    session: Session, client: RateLimitedClient, figure_id: int, bioguide: str, congress: int
) -> int:
    fetch = record_fetch(
        session,
        adapter=ADAPTER,
        native_id=f"{bioguide}-{congress}-marker",
        source_url=f"{BASE}/member/{bioguide}/sponsored-legislation",
        payload={"note": "listing pages fetched live; bills carry their own provenance"},
    )
    written = 0
    for is_original, endpoint, list_key in (
        (True, "sponsored-legislation", "sponsoredLegislation"),
        (False, "cosponsored-legislation", "cosponsoredLegislation"),
    ):
        for item in _pages(client, f"{BASE}/member/{bioguide}/{endpoint}", list_key):
            bill_type = (item.get("type") or "").lower()
            number = item.get("number")
            item_congress = item.get("congress")
            if bill_type not in BILL_SLUGS or not number or item_congress != congress:
                continue  # amendments, reserved numbers, or other congresses
            bill = upsert_bill(
                session, congress=congress, bill_type=bill_type, number=int(number)
            )
            if bill is None:
                continue
            if bill.title is None:
                bill.title = item.get("title")
            if bill.policy_area is None:
                bill.policy_area = (item.get("policyArea") or {}).get("name")
            latest = item.get("latestAction") or {}
            if latest.get("actionDate") and bill.latest_action_date is None:
                bill.latest_action_date = date.fromisoformat(latest["actionDate"])
                bill.latest_action_text = latest.get("text")

            sponsorship = session.scalar(
                select(BillSponsorship).where(
                    BillSponsorship.bill_id == bill.id,
                    BillSponsorship.figure_id == figure_id,
                )
            )
            if sponsorship is None:
                sponsorship = BillSponsorship(bill_id=bill.id, figure_id=figure_id)
                session.add(sponsorship)
            sponsorship.is_original = is_original
            if item.get("introducedDate"):
                sponsorship.sponsored_date = date.fromisoformat(item["introducedDate"])
            sponsorship.source_fetch_id = fetch.id
            written += 1
    session.commit()
    return written


def run(session: Session, limit_members: int | None = None) -> dict:
    client = data_gov_client()  # Congress.gov: 5,000/hr
    congress = get_settings().current_congress
    with ingestion_run(session, ADAPTER) as run_row:
        figures = (
            session.execute(
                select(Figure.id, Figure.bioguide_id)
                .where(Figure.branch == "legislative", Figure.is_active)
                .order_by(Figure.id)
                .limit(limit_members)
            )
        ).all()
        run_row.records_seen = len(figures)

        total = 0
        for figure_id, bioguide in figures:
            total += _ingest_member(session, client, figure_id, bioguide, congress)
        run_row.records_upserted = total
        summary = {
            "adapter": ADAPTER,
            "members": len(figures),
            "sponsorships_upserted": total,
        }
        log.info("%s", summary)
        return summary
