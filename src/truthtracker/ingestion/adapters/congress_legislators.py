"""Seed adapter: unitedstates/congress-legislators → Texas delegation figures.

Canonical member metadata and the external-ID crosswalk (Bioguide ↔ FEC ↔
GovTrack ↔ social handles). Schema holds all 535 + historical; Phase 1 ingests
only members whose current term is for Texas.
"""

import logging
import re
from datetime import date

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from truthtracker.ingestion.base import ingestion_run, record_fetch
from truthtracker.models import ExternalId, Figure, FigureRole

log = logging.getLogger(__name__)

ADAPTER = "congress_legislators"
REPO_RAW = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
LEGISLATORS_URL = f"{REPO_RAW}/legislators-current.yaml"
SOCIAL_URL = f"{REPO_RAW}/legislators-social-media.yaml"

# id-map keys worth crosswalking (fec is a list; bioguide lives on figure itself)
ID_TYPES = ("fec", "govtrack", "opensecrets", "votesmart", "wikidata", "icpsr", "lis", "cspan")

_TITLES = {"rep": "U.S. Representative", "sen": "U.S. Senator"}
_CHAMBERS = {"rep": "house", "sen": "senate"}


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _as_date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _fetch_yaml(url: str) -> list:
    resp = httpx.get(url, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return yaml.safe_load(resp.text)


def _unique_slug(session: Session, base: str, bioguide: str) -> str:
    taken = session.scalar(select(Figure).where(Figure.slug == base))
    if taken is None or taken.bioguide_id == bioguide:
        return base
    return f"{base}-{bioguide.lower()}"


def _upsert_external_id(session: Session, figure: Figure, id_type: str, value, fetch_id: int):
    values = value if isinstance(value, list) else [value]
    for v in values:
        if v is None:
            continue
        v = str(v)
        exists = session.scalar(
            select(ExternalId).where(ExternalId.id_type == id_type, ExternalId.id_value == v)
        )
        if exists is None:
            session.add(
                ExternalId(
                    figure_id=figure.id, id_type=id_type, id_value=v, source_fetch_id=fetch_id
                )
            )


def run(session: Session) -> dict:
    with ingestion_run(session, ADAPTER) as run_row:
        legislators = _fetch_yaml(LEGISLATORS_URL)
        social = _fetch_yaml(SOCIAL_URL)
        run_row.records_seen = len(legislators)

        legis_fetch = record_fetch(
            session,
            adapter=ADAPTER,
            native_id="legislators-current.yaml",
            source_url=LEGISLATORS_URL,
            payload={"records": legislators},
        )
        social_fetch = record_fetch(
            session,
            adapter=ADAPTER,
            native_id="legislators-social-media.yaml",
            source_url=SOCIAL_URL,
            payload={"records": social},
        )
        social_by_bioguide = {
            entry["id"]["bioguide"]: entry.get("social", {})
            for entry in social
            if entry.get("id", {}).get("bioguide")
        }

        today = date.today()
        counts = {"sen": 0, "rep": 0}
        upserted = 0

        for member in legislators:
            ids = member.get("id", {})
            bioguide = ids.get("bioguide")
            current_term = member["terms"][-1]
            term_end = _as_date(current_term.get("end"))
            if current_term.get("state") != "TX" or bioguide is None:
                continue
            if term_end is not None and term_end < today:
                continue

            name = member.get("name", {})
            bio = member.get("bio", {})
            full_name = name.get("official_full") or f"{name.get('first')} {name.get('last')}"

            figure = session.scalar(select(Figure).where(Figure.bioguide_id == bioguide))
            if figure is None:
                figure = Figure(
                    bioguide_id=bioguide,
                    slug=_unique_slug(session, _slugify(full_name), bioguide),
                    branch="legislative",
                )
                session.add(figure)
            figure.full_name = full_name
            figure.first_name = name.get("first")
            figure.last_name = name.get("last")
            figure.birthday = _as_date(bio.get("birthday"))
            figure.official_url = current_term.get("url")
            figure.is_active = True
            figure.source_fetch_id = legis_fetch.id
            session.flush()

            # All terms, not just the current one — history powers drift views.
            for term in member["terms"]:
                term_type = term["type"]
                start = _as_date(term.get("start"))
                role = session.scalar(
                    select(FigureRole).where(
                        FigureRole.figure_id == figure.id,
                        FigureRole.role_type == term_type,
                        FigureRole.start_date == start,
                    )
                )
                if role is None:
                    role = FigureRole(figure_id=figure.id, role_type=term_type, start_date=start)
                    session.add(role)
                role.title = _TITLES.get(term_type, term_type)
                role.chamber = _CHAMBERS.get(term_type)
                role.state = term.get("state")
                role.district = term.get("district")
                role.party = term.get("party")
                role.end_date = _as_date(term.get("end"))
                role.source_url = f"https://bioguide.congress.gov/search/bio/{bioguide}"
                role.source_fetch_id = legis_fetch.id

            for id_type in ID_TYPES:
                if id_type in ids:
                    _upsert_external_id(session, figure, id_type, ids[id_type], legis_fetch.id)
            for handle_type, handle in social_by_bioguide.get(bioguide, {}).items():
                _upsert_external_id(
                    session, figure, f"social_{handle_type}", handle, social_fetch.id
                )

            counts[current_term["type"]] += 1
            upserted += 1

        run_row.records_upserted = upserted
        session.commit()

        summary = {
            "adapter": ADAPTER,
            "senators": counts["sen"],
            "representatives": counts["rep"],
            "figures_upserted": upserted,
        }
        log.info("congress_legislators seed: %s", summary)
        return summary
