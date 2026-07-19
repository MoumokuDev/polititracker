"""Committee assignments from unitedstates/congress-legislators.

Main committees only (4-character thomas ids); subcommittee rows in the
membership file are skipped in Phase 1. Members matched by bioguide.
"""

import logging

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from polititracker.ingestion.base import ingestion_run, record_fetch
from polititracker.ingestion.http import plain_client
from polititracker.ingestion.votes_common import bioguide_figure_map
from polititracker.models import Committee, CommitteeMembership

log = logging.getLogger(__name__)

ADAPTER = "committees"
REPO_RAW = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
COMMITTEES_URL = f"{REPO_RAW}/committees-current.yaml"
MEMBERSHIP_URL = f"{REPO_RAW}/committee-membership-current.yaml"


def run(session: Session) -> dict:
    client = plain_client()
    with ingestion_run(session, ADAPTER) as run_row:
        figures = bioguide_figure_map(session)

        committees_raw = yaml.safe_load(client.get(COMMITTEES_URL).text)
        membership_raw = yaml.safe_load(client.get(MEMBERSHIP_URL).text)
        fetch = record_fetch(
            session,
            adapter=ADAPTER,
            native_id="committees+membership-current",
            source_url=COMMITTEES_URL,
            payload={"committees": committees_raw, "membership": membership_raw},
        )
        run_row.records_seen = len(committees_raw)

        committee_by_thomas: dict[str, Committee] = {}
        for entry in committees_raw:
            thomas_id = entry.get("thomas_id")
            if not thomas_id:
                continue
            committee = session.scalar(
                select(Committee).where(Committee.thomas_id == thomas_id)
            )
            if committee is None:
                committee = Committee(thomas_id=thomas_id)
                session.add(committee)
            committee.name = entry.get("name") or thomas_id
            committee.chamber = entry.get("type") or "joint"
            committee.url = entry.get("url")
            committee.source_fetch_id = fetch.id
            session.flush()
            committee_by_thomas[thomas_id] = committee

        # memberships are replaced wholesale — the YAML is the current roster
        session.execute(delete(CommitteeMembership))
        memberships = 0
        for thomas_id, members in membership_raw.items():
            if len(thomas_id) != 4:  # subcommittees have suffixed ids
                continue
            committee = committee_by_thomas.get(thomas_id)
            if committee is None:
                continue
            for member in members:
                figure_id = figures.get(member.get("bioguide"))
                if figure_id is None:
                    continue
                session.add(
                    CommitteeMembership(
                        committee_id=committee.id,
                        figure_id=figure_id,
                        role_title=member.get("title"),
                        rank=member.get("rank"),
                        party_side=member.get("party"),
                        source_fetch_id=fetch.id,
                    )
                )
                memberships += 1
        session.commit()

        run_row.records_upserted = memberships
        summary = {
            "adapter": ADAPTER,
            "committees": len(committee_by_thomas),
            "memberships_for_tracked_figures": memberships,
        }
        log.info("%s", summary)
        return summary
