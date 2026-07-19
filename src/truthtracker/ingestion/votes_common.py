"""Shared helpers for the vote adapters: figure lookups, bill upserts, position mapping."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthtracker.models import Bill, ExternalId, Figure

# congress.gov URL slugs per bill type code
BILL_SLUGS = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hres": "house-resolution",
    "sres": "senate-resolution",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
}


def bioguide_figure_map(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Figure.bioguide_id, Figure.id).where(Figure.bioguide_id.is_not(None))
    ).all()
    return {b: fid for b, fid in rows}


def lis_figure_map(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(ExternalId.id_value, ExternalId.figure_id).where(ExternalId.id_type == "lis")
    ).all()
    return {v: fid for v, fid in rows}


def normalize_position(raw: str) -> str:
    """Normalized enum for queries; the verbatim string is kept in position_raw."""
    low = (raw or "").strip().lower()
    if low in ("yea", "aye", "yes"):
        return "yea"
    if low in ("nay", "no"):
        return "nay"
    if low.startswith("present"):
        return "present"
    if low in ("not voting", "not-voting"):
        return "not_voting"
    return "other"


def congress_gov_bill_url(congress: int, bill_type: str, number: int) -> str:
    slug = BILL_SLUGS[bill_type]
    return f"https://www.congress.gov/bill/{congress}th-congress/{slug}/{number}"


def upsert_bill(
    session: Session,
    *,
    congress: int,
    bill_type: str,
    number: int,
    source_url: str | None = None,
) -> Bill | None:
    """Minimal bill row from a vote reference; titles/policy areas come from enrich-bills."""
    bill_type = bill_type.lower()
    if bill_type not in BILL_SLUGS:
        return None
    bill = session.scalar(
        select(Bill).where(
            Bill.congress == congress, Bill.bill_type == bill_type, Bill.number == number
        )
    )
    if bill is None:
        bill = Bill(
            congress=congress,
            bill_type=bill_type,
            number=number,
            source_url=source_url or congress_gov_bill_url(congress, bill_type, number),
        )
        session.add(bill)
        session.flush()
    return bill
