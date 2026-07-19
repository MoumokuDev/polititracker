"""House financial-disclosure filing index (Clerk of the House).

One ZIP per year contains the official index of every filing: filer, type,
date, DocID. Rows matched to tracked members become disclosure_filing entries
linking directly to the official PDF on disclosures-clerk.house.gov.

Matching requires BOTH last name and state-district: the index also contains
candidates and other non-member filers for the same districts. The tool lists
filings; interpreting the transactions inside them is left to the reader and
the linked official documents.
"""

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from truthtracker.ingestion.base import ingestion_run, record_fetch
from truthtracker.ingestion.http import plain_client
from truthtracker.models import DisclosureFiling, Figure

log = logging.getLogger(__name__)

ADAPTER = "house_disclosures"
ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
FD_PDF = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{doc_id}.pdf"


def _rep_map(session: Session) -> dict[tuple[str, str], int]:
    """(last_name_lower, 'TX28') → figure_id for current House members we track."""
    figures = (
        session.scalars(
            select(Figure)
            .options(selectinload(Figure.roles))
            .where(Figure.branch == "legislative", Figure.is_active)
        )
    ).all()
    out: dict[tuple[str, str], int] = {}
    for f in figures:
        role = f.roles[-1] if f.roles else None
        if role is None or role.role_type != "rep" or not f.last_name:
            continue
        key = (f.last_name.lower(), f"{role.state}{role.district:02d}")
        out[key] = f.id
    return out


def run(session: Session, years: list[int]) -> dict:
    client = plain_client()
    with ingestion_run(session, ADAPTER) as run_row:
        reps = _rep_map(session)

        seen = 0
        upserted = 0
        for year in years:
            url = ZIP_URL.format(year=year)
            resp = client.get(url)
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
            xml_text = zf.read(xml_name).decode("utf-8-sig", errors="replace")
            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=f"{year}FD.xml",
                source_url=url,
                payload={"xml": xml_text},
            )

            root = ET.fromstring(xml_text)
            for member in root.iter("Member"):
                seen += 1
                last = (member.findtext("Last") or "").strip().lower()
                statedst = (member.findtext("StateDst") or "").strip()
                figure_id = reps.get((last, statedst))
                if figure_id is None:
                    continue
                doc_id = (member.findtext("DocID") or "").strip()
                if not doc_id:
                    continue
                ftype = (member.findtext("FilingType") or "").strip()
                raw_date = (member.findtext("FilingDate") or "").strip()
                filing_date = None
                if raw_date:
                    try:
                        filing_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
                    except ValueError:
                        pass
                pdf = (PTR_PDF if ftype == "P" else FD_PDF).format(year=year, doc_id=doc_id)

                filing = session.scalar(
                    select(DisclosureFiling).where(
                        DisclosureFiling.figure_id == figure_id,
                        DisclosureFiling.doc_id == doc_id,
                    )
                )
                if filing is None:
                    filing = DisclosureFiling(figure_id=figure_id, doc_id=doc_id)
                    session.add(filing)
                filing.filing_type_code = ftype
                filing.filing_year = year
                filing.filing_date = filing_date
                filing.source_url = pdf
                filing.source_fetch_id = fetch.id
                upserted += 1
            session.commit()

        run_row.records_seen = seen
        run_row.records_upserted = upserted
        summary = {
            "adapter": ADAPTER,
            "years": years,
            "index_rows_seen": seen,
            "filings_matched_to_members": upserted,
        }
        log.info("%s", summary)
        return summary
