"""SCOTUS opinions via the CourtListener REST API (v4).

Authorship comes from CourtListener's structured author field (person record),
crosswalked to our justices by name once and cached as an external_id. Each
authored opinion becomes a statement (source_type 'scotus_opinion') whose
source_url prefers the filed PDF on supremecourt.gov — the primary source.
Per-curiam and unattributed opinions are skipped (no single figure to credit).

Rate budget is severe (5/min, 50/hr, 125/day): the client paces at 13s and the
run hard-stops near REQUEST_BUDGET, marking remaining work for the next
(idempotent) run. Bulk data is the right tool for large historical backfills.
"""

import logging
import re
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.config import get_settings
from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import RateLimitedClient
from polititracker.models import ExternalId, Figure, SourcePackage, Statement

log = logging.getLogger(__name__)

ADAPTER = "courtlistener_scotus"
API = "https://www.courtlistener.com/api/rest/v4"
REQUEST_BUDGET = 40  # stay safely inside 50/hr

OPINION_TYPE_LABELS = {
    "010combined": "Opinion",
    "015unamimous": "Unanimous opinion",
    "020lead": "Majority opinion",
    "025plurality": "Plurality opinion",
    "030concurrence": "Concurrence",
    "035concurrenceinpart": "Concurrence in part",
    "040dissent": "Dissent",
}


def _client() -> RateLimitedClient:
    token = get_settings().courtlistener_api_token
    return RateLimitedClient(
        min_interval=13.0, headers={"Authorization": f"Token {token}"}
    )


def _opinion_id(url: str) -> str | None:
    m = re.search(r"/opinions/(\d+)/", url)
    return m.group(1) if m else None


def _person_id(url: str) -> str | None:
    m = re.search(r"/people/(\d+)/", url)
    return m.group(1) if m else None


def _justice_map(session: Session) -> dict[str, int]:
    """CL person id → figure id, from previously cached crosswalk rows."""
    rows = session.execute(
        select(ExternalId.id_value, ExternalId.figure_id).where(
            ExternalId.id_type == "courtlistener_person"
        )
    ).all()
    return {v: fid for v, fid in rows}


def _resolve_author(
    session: Session, client: RateLimitedClient, person_url: str, cache: dict[str, int]
) -> int | None:
    person_id = _person_id(person_url)
    if person_id is None:
        return None
    if person_id in cache:
        return cache[person_id]
    person = client.get(person_url, fields="id,name_first,name_last").json()
    figure = session.scalar(
        select(Figure).where(
            Figure.branch == "judicial", Figure.last_name == person.get("name_last")
        )
    )
    if figure is None:
        cache[person_id] = None  # not a sitting justice we track
        return None
    session.add(
        ExternalId(figure_id=figure.id, id_type="courtlistener_person", id_value=person_id)
    )
    session.flush()
    cache[person_id] = figure.id
    return figure.id


def run(session: Session, since: date, limit: int = 10) -> dict:
    client = _client()
    with ingestion_run(session, ADAPTER) as run_row:
        author_cache: dict[str, int | None] = dict(_justice_map(session))

        clusters = client.get(
            f"{API}/clusters/",
            docket__court="scotus",
            date_filed__gte=since.isoformat(),
            order_by="-date_filed",
            page_size=limit,
        ).json()
        results = clusters.get("results", [])
        run_row.records_seen = len(results)

        statements_upserted = 0
        skipped_budget = False
        for cluster in results:
            if client.request_count >= REQUEST_BUDGET:
                skipped_budget = True
                break
            cluster_id = str(cluster["id"])
            date_filed = date.fromisoformat(cluster["date_filed"])
            case_name = cluster.get("case_name") or f"Cluster {cluster_id}"

            pending = []
            for op_url in cluster.get("sub_opinions", []):
                op_id = _opinion_id(op_url)
                if op_id is None:
                    continue
                exists = session.scalar(
                    select(Statement.id).where(
                        Statement.source_type == "scotus_opinion",
                        Statement.native_id == f"cl-op-{op_id}",
                    )
                )
                if exists is None:
                    pending.append((op_id, op_url))
            if not pending:
                continue

            cluster_statements = 0
            for op_id, op_url in pending:
                if client.request_count >= REQUEST_BUDGET:
                    skipped_budget = True
                    break
                op = client.get(
                    op_url,
                    fields="id,author,author_str,type,per_curiam,plain_text,download_url",
                ).json()
                if op.get("per_curiam") or not op.get("author"):
                    continue
                figure_id = _resolve_author(session, client, op["author"], author_cache)
                if figure_id is None:
                    continue
                text = (op.get("plain_text") or "").strip()
                if not text:
                    log.info("opinion %s has no plain_text; skipping", op_id)
                    continue

                fetch = record_fetch(
                    session,
                    adapter=ADAPTER,
                    native_id=f"cl-op-{op_id}",
                    source_url=f"https://www.courtlistener.com{cluster.get('absolute_url', '')}",
                    payload={"opinion_meta": {k: v for k, v in op.items() if k != "plain_text"},
                             "plain_text": text},
                )
                type_label = OPINION_TYPE_LABELS.get(op.get("type"), op.get("type") or "Opinion")
                stmt = Statement(
                    source_type="scotus_opinion",
                    native_id=f"cl-op-{op_id}",
                    figure_id=figure_id,
                    utterance_text=text,
                    occurred_on=date_filed,
                    source_url=op.get("download_url")
                    or f"https://www.courtlistener.com{cluster.get('absolute_url', '')}",
                    heading=f"{case_name} — {type_label}",
                    confidence=1.0,
                    attribution_method="courtlistener_author",
                    source_fetch_id=fetch.id,
                )
                session.add(stmt)
                session.flush()
                cluster_statements += 1
                statements_upserted += 1

            pkg = session.scalar(
                select(SourcePackage).where(
                    SourcePackage.adapter == ADAPTER,
                    SourcePackage.package_id == f"cluster-{cluster_id}",
                )
            )
            if pkg is None:
                pkg = SourcePackage(
                    adapter=ADAPTER, package_id=f"cluster-{cluster_id}", issue_date=date_filed
                )
                session.add(pkg)
            pkg.status = "ingested"
            pkg.statement_count = cluster_statements
            pkg.fetched_at = datetime.now(UTC)
            session.commit()

        run_row.records_upserted = statements_upserted
        summary = {
            "adapter": ADAPTER,
            "clusters_seen": len(results),
            "statements_upserted": statements_upserted,
            "requests_used": client.request_count,
            "stopped_at_budget": skipped_budget,
        }
        log.info("%s", summary)
        return summary
