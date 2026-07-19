"""Senate roll-call votes via senate.gov LIS XML (not yet in the Congress.gov API).

Member positions crosswalk through LIS ids (external_id.id_type == 'lis',
seeded from congress-legislators). position_raw keeps forms like
"Present, Giving Live Pair" verbatim.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import plain_client
from polititracker.ingestion.votes_common import (
    lis_figure_map,
    normalize_position,
    upsert_bill,
)
from polititracker.models import RollCall, VoteCast

log = logging.getLogger(__name__)

ADAPTER = "senate_votes"
MENU_URL = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
VOTE_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes/"
    "vote{congress}{session}/vote_{congress}_{session}_{number:05d}.xml"
)

# "S.J.Res. 198" → ("sjres", 198); "PN851-8" (nomination) → no bill row
_ISSUE_RE = re.compile(
    r"^(S|H\.R|S\.Res|H\.Res|S\.J\.Res|H\.J\.Res|S\.Con\.Res|H\.Con\.Res)\.?\s+(\d+)"
)


def _issue_to_bill(issue: str) -> tuple[str, int] | None:
    m = _ISSUE_RE.match((issue or "").strip())
    if not m:
        return None
    return m.group(1).replace(".", "").lower(), int(m.group(2))


def _parse_vote_datetime(text: str) -> datetime | None:
    try:
        return datetime.strptime(re.sub(r"\s+", " ", text or "").strip(), "%B %d, %Y, %I:%M %p")
    except ValueError:
        return None


def run(
    session: Session, congress: int, session_number: int, limit: int | None = None
) -> dict:
    client = plain_client()
    with ingestion_run(session, ADAPTER) as run_row:
        lis_map = lis_figure_map(session)

        menu_xml = client.get(MENU_URL.format(congress=congress, session=session_number)).text
        menu = ET.fromstring(menu_xml)
        entries = []
        for v in menu.iter("vote"):
            num = v.findtext("vote_number")
            if num and num.strip().isdigit():
                entries.append(
                    {"number": int(num), "result": (v.findtext("result") or "").strip()}
                )
        entries.sort(key=lambda e: e["number"], reverse=True)
        if limit:
            entries = entries[:limit]
        run_row.records_seen = len(entries)

        upserted = 0
        for entry in entries:
            number = entry["number"]
            roll = session.scalar(
                select(RollCall).where(
                    RollCall.congress == congress,
                    RollCall.chamber == "senate",
                    RollCall.session == session_number,
                    RollCall.roll_number == number,
                )
            )
            if roll is not None and roll.source_fetch_id is not None:
                continue

            url = VOTE_URL.format(congress=congress, session=session_number, number=number)
            xml_text = client.get(url).text
            detail = ET.fromstring(xml_text)

            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=f"{congress}-{session_number}-{number}",
                source_url=url,
                payload={"xml": xml_text},
            )

            bill = None
            parsed = _issue_to_bill(detail.findtext("issue") or "")
            if parsed:
                bill = upsert_bill(
                    session, congress=congress, bill_type=parsed[0], number=parsed[1]
                )

            when = _parse_vote_datetime(detail.findtext("vote_date") or "")
            if when is None:
                raise ValueError(f"unparseable senate vote_date for vote {number}")

            if roll is None:
                roll = RollCall(
                    congress=congress,
                    chamber="senate",
                    session=session_number,
                    roll_number=number,
                )
                session.add(roll)
            roll.question = (detail.findtext("vote_question_text") or "").strip() or None
            roll.result = (detail.findtext("vote_result") or "").strip() or entry["result"]
            roll.vote_date = when.date()
            roll.bill_id = bill.id if bill else None
            roll.source_url = url
            roll.source_fetch_id = fetch.id
            session.flush()

            for member in detail.iter("member"):
                lis_id = (member.findtext("lis_member_id") or "").strip()
                figure_id = lis_map.get(lis_id)
                if figure_id is None:
                    continue
                raw = (member.findtext("vote_cast") or "").strip()
                cast = session.get(VoteCast, (roll.id, figure_id))
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
        summary = {"adapter": ADAPTER, "votes_seen": len(entries), "votes_ingested": upserted}
        log.info("%s", summary)
        return summary
