"""House roll-call votes via the Congress.gov API (house-vote beta endpoint).

Positions are stored for every figure we track (bioguide match); the roll_call
row itself is chamber-wide. source_url points at the Clerk's XML — the primary
source — and the full member-vote API payload is kept in source_fetch.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import data_gov_client
from polititracker.ingestion.votes_common import (
    bioguide_figure_map,
    normalize_position,
    upsert_bill,
)
from polititracker.models import RollCall, VoteCast

log = logging.getLogger(__name__)

ADAPTER = "house_votes"
BASE = "https://api.congress.gov/v3"


def run(
    session: Session, congress: int, session_number: int, limit: int | None = None
) -> dict:
    client = data_gov_client()
    with ingestion_run(session, ADAPTER) as run_row:
        figures = bioguide_figure_map(session)

        votes = []
        offset = 0
        while True:
            page = client.get(
                f"{BASE}/house-vote/{congress}/{session_number}",
                format="json",
                offset=offset,
                limit=250,
            ).json()
            items = page.get("houseRollCallVotes", [])
            votes.extend(items)
            offset += 250
            if not items or offset >= page.get("pagination", {}).get("count", 0):
                break

        votes.sort(key=lambda v: v["rollCallNumber"], reverse=True)
        if limit:
            votes = votes[:limit]
        run_row.records_seen = len(votes)

        upserted = 0
        for v in votes:
            roll_number = v["rollCallNumber"]
            roll = session.scalar(
                select(RollCall).where(
                    RollCall.congress == congress,
                    RollCall.chamber == "house",
                    RollCall.session == session_number,
                    RollCall.roll_number == roll_number,
                )
            )
            if roll is not None and roll.source_fetch_id is not None:
                continue  # already fully ingested

            detail = client.get(
                f"{BASE}/house-vote/{congress}/{session_number}/{roll_number}/members",
                format="json",
            ).json()["houseRollCallVoteMemberVotes"]

            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=f"{congress}-{session_number}-{roll_number}",
                source_url=v.get("sourceDataURL")
                or f"{BASE}/house-vote/{congress}/{session_number}/{roll_number}",
                payload=detail,
            )

            bill = None
            leg_type, leg_num = detail.get("legislationType"), detail.get("legislationNumber")
            if leg_type and leg_num and str(leg_num).isdigit():
                bill = upsert_bill(
                    session,
                    congress=congress,
                    bill_type=str(leg_type),
                    number=int(leg_num),
                    source_url=detail.get("legislationUrl"),
                )

            if roll is None:
                roll = RollCall(
                    congress=congress,
                    chamber="house",
                    session=session_number,
                    roll_number=roll_number,
                )
                session.add(roll)
            roll.question = detail.get("voteQuestion")
            roll.result = detail.get("result")
            roll.vote_date = date.fromisoformat(str(detail.get("startDate"))[:10])
            roll.bill_id = bill.id if bill else None
            roll.source_url = v.get("sourceDataURL") or ""
            roll.source_fetch_id = fetch.id
            session.flush()

            for member in detail.get("results", []):
                figure_id = figures.get(member.get("bioguideID"))
                if figure_id is None:
                    continue
                cast = session.get(VoteCast, (roll.id, figure_id))
                raw = member.get("voteCast", "")
                if cast is None:
                    cast = VoteCast(roll_call_id=roll.id, figure_id=figure_id)
                    session.add(cast)
                cast.position = normalize_position(raw)
                cast.position_raw = raw
            upserted += 1
            if upserted % 25 == 0:
                session.commit()

        run_row.records_upserted = upserted
        session.commit()
        summary = {"adapter": ADAPTER, "votes_seen": len(votes), "votes_ingested": upserted}
        log.info("%s", summary)
        return summary
