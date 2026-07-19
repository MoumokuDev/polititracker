"""Official congressional portraits, downloaded locally with provenance.

Source: the unitedstates/images project's mirror of Congressional Pictorial
Directory / Biographical Directory portraits (public-domain government works),
addressable by bioguide ID. Figures without a bioguide portrait (most executive
and all judicial figures) render as neutral monograms until a verified
`portrait_url` is added to their seed entry.

Downloads land in the API's static dir with a manifest.json recording source
URL, sha256, and fetch time for every file — no image without provenance.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthtracker.ingestion.base import ingestion_run
from truthtracker.ingestion.http import plain_client
from truthtracker.models import Figure

log = logging.getLogger(__name__)

ADAPTER = "portraits"
SOURCE_URL = "https://unitedstates.github.io/images/congress/450x550/{bioguide}.jpg"
_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "api" / "static" / "portraits"


def portraits_dir() -> Path:
    from truthtracker.config import get_settings

    configured = get_settings().portraits_dir
    return Path(configured) if configured else _DEFAULT_DIR


def run(session: Session) -> dict:
    client = plain_client()
    target_dir = portraits_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / "manifest.json"
    manifest: dict = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )

    with ingestion_run(session, ADAPTER) as run_row:
        bioguides = session.scalars(
            select(Figure.bioguide_id).where(Figure.bioguide_id.is_not(None))
        ).all()
        run_row.records_seen = len(bioguides)

        fetched = 0
        for bioguide in bioguides:
            target = target_dir / f"{bioguide}.jpg"
            if target.exists() and manifest.get(bioguide, {}).get("status") == "ok":
                continue
            url = SOURCE_URL.format(bioguide=bioguide)
            try:
                resp = client.get(url)
            except Exception as exc:  # 404 for members without a mirrored portrait
                manifest[bioguide] = {
                    "status": "missing",
                    "source_url": url,
                    "error": f"{type(exc).__name__}",
                    "checked_at": datetime.now(UTC).isoformat(),
                }
                continue
            target.write_bytes(resp.content)
            manifest[bioguide] = {
                "status": "ok",
                "source_url": url,
                "sha256": hashlib.sha256(resp.content).hexdigest(),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            fetched += 1

        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        run_row.records_upserted = fetched
        ok = sum(1 for m in manifest.values() if m.get("status") == "ok")
        missing = sum(1 for m in manifest.values() if m.get("status") == "missing")
        summary = {
            "adapter": ADAPTER,
            "figures_with_bioguide": len(bioguides),
            "portraits_fetched_now": fetched,
            "portraits_available": ok,
            "portraits_missing_upstream": missing,
        }
        log.info("%s", summary)
        return summary


def available_portraits() -> set[str]:
    """Bioguide ids that have a locally stored portrait."""
    directory = portraits_dir()
    if not directory.exists():
        return set()
    return {p.stem for p in directory.glob("*.jpg")}
