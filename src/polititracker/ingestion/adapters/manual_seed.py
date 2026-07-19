"""Seed adapter: manually verified executive and judicial figures.

Data lives in seeds/*.yaml with per-entry source URLs and a file-level
verification note. These files are the raw payload; the adapter stores them in
source_fetch like any other source, so even hand-entered rows have provenance.
"""

import logging
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.models import ExternalId, Figure, FigureRole

log = logging.getLogger(__name__)

ADAPTER = "manual_seed"
SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds"
SEED_FILES = ("executive_figures.yaml", "judicial_figures.yaml")


def _as_date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def run(session: Session) -> dict:
    with ingestion_run(session, ADAPTER) as run_row:
        upserted = 0
        seen = 0
        for filename in SEED_FILES:
            path = SEEDS_DIR / filename
            entries = yaml.safe_load(path.read_text(encoding="utf-8"))
            seen += len(entries)
            fetch = record_fetch(
                session,
                adapter=ADAPTER,
                native_id=filename,
                source_url=f"file://seeds/{filename}",
                payload={"records": entries},
            )

            for entry in entries:
                figure = session.scalar(select(Figure).where(Figure.slug == entry["slug"]))
                if figure is None:
                    figure = Figure(slug=entry["slug"], branch=entry["branch"])
                    session.add(figure)
                figure.full_name = entry["full_name"]
                figure.first_name = entry.get("first_name")
                figure.last_name = entry.get("last_name")
                figure.branch = entry["branch"]
                figure.official_url = entry.get("official_url")
                figure.bioguide_id = entry.get("external_ids", {}).get("bioguide")
                figure.is_active = True
                figure.source_fetch_id = fetch.id
                session.flush()

                role_data = entry["role"]
                start = _as_date(role_data.get("start_date"))
                role = session.scalar(
                    select(FigureRole).where(
                        FigureRole.figure_id == figure.id,
                        FigureRole.role_type == role_data["role_type"],
                        FigureRole.start_date == start,
                    )
                )
                if role is None:
                    role = FigureRole(
                        figure_id=figure.id,
                        role_type=role_data["role_type"],
                        start_date=start,
                    )
                    session.add(role)
                role.title = role_data["title"]
                role.party = role_data.get("party")
                role.is_acting = bool(role_data.get("is_acting", False))
                role.end_date = _as_date(role_data.get("end_date"))
                role.source_url = role_data["source_url"]
                role.source_fetch_id = fetch.id

                for id_type, id_value in entry.get("external_ids", {}).items():
                    if id_type == "bioguide" or id_value is None:
                        continue
                    exists = session.scalar(
                        select(ExternalId).where(
                            ExternalId.id_type == id_type,
                            ExternalId.id_value == str(id_value),
                        )
                    )
                    if exists is None:
                        session.add(
                            ExternalId(
                                figure_id=figure.id,
                                id_type=id_type,
                                id_value=str(id_value),
                                source_fetch_id=fetch.id,
                            )
                        )
                upserted += 1

        run_row.records_seen = seen
        run_row.records_upserted = upserted
        session.commit()

        summary = {"adapter": ADAPTER, "figures_upserted": upserted}
        log.info("manual seed: %s", summary)
        return summary
