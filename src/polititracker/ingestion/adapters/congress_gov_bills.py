"""Bill enrichment: titles, policy areas, latest actions, and official summaries.

Summaries are the Congressional Research Service's (via the Congress.gov API) —
government-authored, displayed with attribution. This project never generates
summaries itself. Policy areas are the Phase 1 topic taxonomy (build step 6).
"""

import html as html_lib
import logging
import re
from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import data_gov_client
from polititracker.models import Bill

log = logging.getLogger(__name__)

ADAPTER = "congress_gov_bills"
BASE = "https://api.congress.gov/v3"


def _clean_summary(text: str) -> str:
    """CRS summary HTML → readable plain text (paragraph breaks preserved)."""
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text).strip()


def run(session: Session, limit: int = 100) -> dict:
    client = data_gov_client()  # Congress.gov budget: 5,000/hr
    with ingestion_run(session, ADAPTER) as run_row:
        bills = (
            session.scalars(
                select(Bill)
                .where(or_(Bill.title.is_(None), Bill.summary_checked_at.is_(None)))
                .order_by(Bill.id)
                .limit(limit)
            )
        ).all()
        run_row.records_seen = len(bills)

        enriched = 0
        for bill in bills:
            ref = f"{bill.congress}/{bill.bill_type}/{bill.number}"
            data = client.get(f"{BASE}/bill/{ref}", format="json").json()["bill"]
            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=f"{bill.congress}-{bill.bill_type}-{bill.number}",
                source_url=bill.source_url,
                payload=data,
            )
            bill.title = data.get("title")
            bill.policy_area = (data.get("policyArea") or {}).get("name")
            latest = data.get("latestAction") or {}
            if latest.get("actionDate"):
                bill.latest_action_date = date.fromisoformat(latest["actionDate"])
            bill.latest_action_text = latest.get("text")
            bill.source_fetch_id = fetch.id

            summaries = (
                client.get(f"{BASE}/bill/{ref}/summaries", format="json")
                .json()
                .get("summaries", [])
            )
            if summaries:
                latest_summary = max(summaries, key=lambda s: s.get("updateDate") or "")
                record_fetch(
                    session,
                    adapter=ADAPTER,
                    native_id=f"{bill.congress}-{bill.bill_type}-{bill.number}/summaries",
                    source_url=bill.source_url,
                    payload={"summaries": summaries},
                )
                bill.summary = _clean_summary(latest_summary.get("text") or "") or None
                if latest_summary.get("actionDate"):
                    bill.summary_date = date.fromisoformat(latest_summary["actionDate"])
            bill.summary_checked_at = datetime.now(UTC)

            enriched += 1
            if enriched % 25 == 0:
                session.commit()

        session.commit()
        run_row.records_upserted = enriched
        summary_out = {"adapter": ADAPTER, "bills_enriched": enriched}
        log.info("%s", summary_out)
        return summary_out
