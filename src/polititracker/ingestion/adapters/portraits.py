"""Official portraits, downloaded locally with provenance. Files are {slug}.jpg.

Three sources, most authoritative first, all recorded in manifest.json
(source, source URL, license, attribution, sha256, fetch time):

1. unitedstates/images mirror of Congressional Pictorial Directory portraits
   (public-domain government works), by bioguide id.
2. Congress.gov member depiction images (official member photos, with the
   attribution string the API provides), by bioguide id.
3. Wikidata image (P18) → Wikimedia Commons, for figures without a bioguide
   (executive/judicial). The entity must match the figure's name AND its
   description must contain a role keyword derived from their title — a plain
   name search is not safe (the top "John Roberts" on Wikidata is a comedian).
   Only Public-domain / CC0 images are accepted; anything needing attribution
   or with an unclear license is skipped and the monogram remains.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from polititracker.ingestion.base import ingestion_run
from polititracker.ingestion.http import RateLimitedClient, data_gov_client, plain_client
from polititracker.models import Figure

log = logging.getLogger(__name__)

ADAPTER = "portraits"
MIRROR_URL = "https://unitedstates.github.io/images/congress/450x550/{bioguide}.jpg"
CONGRESS_API = "https://api.congress.gov/v3"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# prefix match against Commons LicenseShortName, lowercased ("Public domain",
# "PD US Government", "PD-USGov", "CC0", ...) — anything else is skipped
ACCEPTED_LICENSE_PREFIXES = ("public domain", "cc0", "pd")

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "api" / "static" / "portraits"


def portraits_dir() -> Path:
    from polititracker.config import get_settings

    configured = get_settings().portraits_dir
    return Path(configured) if configured else _DEFAULT_DIR


def _role_keywords(title: str) -> list[str]:
    """Keywords the Wikidata entity description must contain, from the role title."""
    t = (title or "").lower()
    words = [t]
    if "chief justice" in t:
        words.append("chief justice")
    elif "justice" in t:
        words.append("justice")
    if "secretary" in t:
        words.append(re.sub(r"^secretary of (the )?", "secretary of ", t))
    if "attorney general" in t:
        words.append("attorney general")
    if "president" in t:
        words.append("president")
    return words


def _wikidata_image(
    client: RateLimitedClient, full_name: str, title: str
) -> tuple[str, str, str, str] | None:
    """(download_url, source_page, license, attribution) for a safe P18, or None."""
    search = client.get(
        WIKIDATA_API,
        action="wbsearchentities",
        search=full_name,
        language="en",
        format="json",
        limit=5,
    ).json()
    keywords = _role_keywords(title)
    matches = [
        e
        for e in search.get("search", [])
        if any(k in (e.get("description") or "").lower() for k in keywords)
    ]
    if len(matches) != 1:
        return None  # ambiguous or absent — do not guess
    qid = matches[0]["id"]

    claims = client.get(
        WIKIDATA_API, action="wbgetclaims", entity=qid, property="P18", format="json"
    ).json()
    p18 = claims.get("claims", {}).get("P18", [])
    if not p18:
        return None
    filename = p18[0]["mainsnak"]["datavalue"]["value"]

    info = client.get(
        COMMONS_API,
        action="query",
        titles=f"File:{filename}",
        prop="imageinfo",
        iiprop="extmetadata|url",
        iiurlwidth="450",
        format="json",
    ).json()
    pages = info.get("query", {}).get("pages", {})
    imageinfo = next(iter(pages.values()), {}).get("imageinfo", [{}])[0]
    meta = imageinfo.get("extmetadata", {})
    license_name = (meta.get("LicenseShortName", {}).get("value") or "").strip()
    if not license_name.lower().startswith(ACCEPTED_LICENSE_PREFIXES):
        log.info("skipping %s: license %r needs attribution/unclear", full_name, license_name)
        return None
    artist = re.sub(r"<[^>]+>", "", str(meta.get("Artist", {}).get("value") or "")).strip()
    url = imageinfo.get("thumburl") or imageinfo.get("url")
    if not url:
        return None
    source_page = f"https://commons.wikimedia.org/wiki/File:{filename.replace(' ', '_')}"
    return url, source_page, license_name, artist


def _congress_depiction(client: RateLimitedClient, bioguide: str) -> tuple[str, str] | None:
    member = client.get(
        f"{CONGRESS_API}/member/{bioguide}", format="json"
    ).json().get("member", {})
    depiction = member.get("depiction") or {}
    if not depiction.get("imageUrl"):
        return None
    return depiction["imageUrl"], depiction.get("attribution") or "Congress.gov member image"


def run(session: Session, refresh: bool = False) -> dict:
    plain = plain_client()
    congress = data_gov_client()
    target_dir = portraits_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / "manifest.json"
    manifest: dict = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )

    with ingestion_run(session, ADAPTER) as run_row:
        figures = (
            session.scalars(
                select(Figure)
                .options(selectinload(Figure.roles))
                .where(Figure.is_active)
                .order_by(Figure.id)
            )
        ).all()
        run_row.records_seen = len(figures)

        fetched = 0
        for figure in figures:
            target = target_dir / f"{figure.slug}.jpg"
            entry = manifest.get(figure.slug, {})
            if target.exists() and entry.get("status") == "ok" and not refresh:
                continue

            content: bytes | None = None
            record: dict = {}
            if figure.bioguide_id:
                try:
                    resp = plain.get(MIRROR_URL.format(bioguide=figure.bioguide_id))
                    content = resp.content
                    record = {
                        "source": "unitedstates_images",
                        "source_url": MIRROR_URL.format(bioguide=figure.bioguide_id),
                        "license": "Public domain (US government work)",
                    }
                except Exception:
                    dep = None
                    try:
                        dep = _congress_depiction(congress, figure.bioguide_id)
                    except Exception:
                        pass
                    if dep:
                        content = plain.get(dep[0]).content
                        record = {
                            "source": "congress_gov_depiction",
                            "source_url": dep[0],
                            "attribution": dep[1],
                        }
            else:
                role = figure.roles[-1] if figure.roles else None
                try:
                    found = _wikidata_image(
                        plain, figure.full_name, role.title if role else ""
                    )
                except Exception as exc:
                    log.info("wikidata lookup failed for %s: %s", figure.slug, exc)
                    found = None
                if found:
                    url, source_page, license_name, artist = found
                    content = plain.get(url).content
                    record = {
                        "source": "wikimedia_commons",
                        "source_url": source_page,
                        "license": license_name,
                        "attribution": artist,
                    }

            if content is None:
                manifest[figure.slug] = {
                    "status": "missing",
                    "checked_at": datetime.now(UTC).isoformat(),
                }
                continue
            target.write_bytes(content)
            manifest[figure.slug] = {
                "status": "ok",
                **record,
                "sha256": hashlib.sha256(content).hexdigest(),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            fetched += 1

        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        run_row.records_upserted = fetched
        ok = sum(1 for m in manifest.values() if m.get("status") == "ok")
        summary = {
            "adapter": ADAPTER,
            "figures": len(figures),
            "fetched_now": fetched,
            "available": ok,
            "missing": sum(1 for m in manifest.values() if m.get("status") == "missing"),
        }
        log.info("%s", summary)
        return summary


def available_portraits() -> set[str]:
    """Figure slugs that have a locally stored portrait."""
    directory = portraits_dir()
    if not directory.exists():
        return set()
    return {p.stem for p in directory.glob("*.jpg")}
