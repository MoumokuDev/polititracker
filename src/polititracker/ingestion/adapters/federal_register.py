"""Presidential documents via the Federal Register API (no key required).

Executive orders, proclamations, memoranda — signed presidential documents,
attributed to the signing president (matched by name against our executive
figures). Full raw text becomes a statement (source_type 'fedreg_presdoc');
source_url is the document's federalregister.gov page. Pagination is capped at
2,000 results per query upstream, so ingestion is date-windowed.
"""

import logging
import re
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import RateLimitedClient
from polititracker.models import Figure, SourcePackage, Statement

log = logging.getLogger(__name__)

ADAPTER = "federal_register"
API = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = (
    "document_number",
    "title",
    "type",
    "president",
    "signing_date",
    "publication_date",
    "html_url",
    "raw_text_url",
    "executive_order_number",
)


def _strip_markup(text: str) -> str:
    text = text.replace("\x00", "")  # NUL bytes appear in some FR text renditions
    if "<" in text and re.search(r"<(html|body|pre|p|div)\b", text, re.I):
        text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _president_figures(session: Session) -> dict[str, int]:
    """lowercase president name → figure id, for executive figures."""
    figures = session.scalars(select(Figure).where(Figure.branch == "executive")).all()
    return {f.full_name.lower(): f.id for f in figures} | {
        (f.first_name + " " + f.last_name).lower(): f.id
        for f in figures
        if f.first_name and f.last_name
    }


def run(session: Session, since: date, limit: int = 50) -> dict:
    client = RateLimitedClient(min_interval=0.6)
    with ingestion_run(session, ADAPTER) as run_row:
        presidents = _president_figures(session)

        params = [
            ("conditions[type][]", "PRESDOCU"),
            ("conditions[publication_date][gte]", since.isoformat()),
            ("per_page", str(min(limit, 100))),
            ("order", "newest"),
        ] + [("fields[]", f) for f in FIELDS]
        data = client.get(API, params_list=params).json()
        results = data.get("results", [])[:limit]
        run_row.records_seen = len(results)

        upserted = 0
        for doc in results:
            doc_number = doc.get("document_number")
            if not doc_number:
                continue
            exists = session.scalar(
                select(Statement.id).where(
                    Statement.source_type == "fedreg_presdoc",
                    Statement.native_id == doc_number,
                )
            )
            if exists is not None:
                continue

            president = doc.get("president") or {}
            name = (
                president.get("name") if isinstance(president, dict) else str(president or "")
            ) or ""
            figure_id = presidents.get(name.lower())
            if figure_id is None:
                log.info("document %s signed by untracked president %r; skipped", doc_number, name)
                continue

            raw_text = ""
            if doc.get("raw_text_url"):
                raw_text = _strip_markup(client.get(doc["raw_text_url"]).text)
            if not raw_text:
                log.info("document %s has no raw text; skipped", doc_number)
                continue

            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=doc_number,
                source_url=doc.get("html_url") or API,
                payload={"document": doc, "raw_text": raw_text},
            )
            occurred = date.fromisoformat(doc.get("signing_date") or doc["publication_date"])
            eo = doc.get("executive_order_number")
            heading = (f"Executive Order {eo} — " if eo else "") + (doc.get("title") or doc_number)
            session.add(
                Statement(
                    source_type="fedreg_presdoc",
                    native_id=doc_number,
                    figure_id=figure_id,
                    utterance_text=raw_text,
                    occurred_on=occurred,
                    source_url=doc.get("html_url") or API,
                    heading=heading,
                    confidence=1.0,
                    attribution_method="federal_register_president",
                    source_fetch_id=fetch.id,
                )
            )
            session.flush()

            pkg = session.scalar(
                select(SourcePackage).where(
                    SourcePackage.adapter == ADAPTER, SourcePackage.package_id == doc_number
                )
            )
            if pkg is None:
                pkg = SourcePackage(
                    adapter=ADAPTER,
                    package_id=doc_number,
                    issue_date=date.fromisoformat(doc["publication_date"]),
                )
                session.add(pkg)
            pkg.status = "ingested"
            pkg.statement_count = 1
            pkg.fetched_at = datetime.now(UTC)
            upserted += 1
            if upserted % 10 == 0:
                session.commit()

        session.commit()
        run_row.records_upserted = upserted
        summary = {
            "adapter": ADAPTER,
            "documents_seen": len(results),
            "statements_upserted": upserted,
        }
        log.info("%s", summary)
        return summary
