"""Congressional Record parsing: MODS granule index + speaker-turn extraction.

Pure functions, no I/O — unit-tested against captured fixtures.

Anti-fabrication invariant: every extracted utterance is a verbatim substring
of the cleaned granule text it came from; extract_turns raises if not.
"""

import html as html_lib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class GranuleMeta:
    granule_id: str
    granule_class: str
    sub_class: str | None = None
    title: str | None = None
    date: str | None = None
    # (bioguide_id, parsed_name) e.g. ("G000553", "Mr. GREEN of Texas")
    members: list[tuple[str, str]] = field(default_factory=list)


def parse_mods_granules(mods_xml: str) -> list[GranuleMeta]:
    """Index all constituent granules of a CREC package from its MODS document."""
    root = ET.fromstring(mods_xml)
    out: list[GranuleMeta] = []
    for ri in root.iter():
        if _local(ri.tag) != "relatedItem" or ri.get("type") != "constituent":
            continue
        gid = (ri.get("ID") or "").removeprefix("id-")
        if not gid:
            continue
        meta = GranuleMeta(granule_id=gid, granule_class="")
        for el in ri.iter():
            ln = _local(el.tag)
            if ln == "granuleClass":
                meta.granule_class = el.text or ""
            elif ln == "subGranuleClass":
                meta.sub_class = el.text
            elif ln == "searchTitle":
                meta.title = el.text
            elif ln == "granuleDate":
                meta.date = el.text
            elif ln == "congMember":
                bioguide = el.get("bioGuideId")
                parsed = next(
                    (
                        nm.text
                        for nm in el
                        if _local(nm.tag) == "name" and nm.get("type") == "parsed" and nm.text
                    ),
                    None,
                )
                if bioguide and parsed:
                    meta.members.append((bioguide, parsed.strip()))
        out.append(meta)
    return out


def granule_plain_text(htm: str) -> str:
    """The <pre> body of a granule htm file, tags stripped, entities unescaped."""
    m = re.search(r"<pre>(.*)</pre>", htm, re.S)
    body = m.group(1) if m else htm
    body = re.sub(r"<[^>]+>", "", body)
    return html_lib.unescape(body)


# A speaker header opens a paragraph: exactly two spaces of indent, an
# honorific + capitalized surname (or a presiding-officer form), a period.
_HONORIFIC = r"(?:Mr|Mrs|Ms|Miss|Dr)"
_ANY_SPEAKER = (
    rf"{_HONORIFIC}\. [A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-]+)*"
    r"(?: of [A-Z][a-z]+(?: [A-Z][a-z]+)?)?"
    r"|The (?:ACTING |VICE )?"
    r"(?:SPEAKER|PRESIDENT|PRESIDING OFFICER|CHIEF JUSTICE|CHAIR(?:MAN)?|CLERK)"
    r"(?: pro tempore)?"
)
SPEAKER_BOUNDARY = re.compile(rf"^  (?! )(?:{_ANY_SPEAKER})\.(?= )", re.M)


@dataclass
class SpeakerTurn:
    bioguide: str
    parsed_name: str
    text: str
    start: int
    turn_index: int = 0


def extract_turns(text: str, members: list[tuple[str, str]]) -> list[SpeakerTurn]:
    """Extract each tracked member's speaking turns from cleaned granule text.

    A turn runs from the member's speaker header to the next speaker header.
    Procedural narration inside that span (e.g. "The Clerk read...") remains —
    the Record is reproduced verbatim, never edited.
    """
    boundaries = [m.start() for m in SPEAKER_BOUNDARY.finditer(text)]
    turns: list[SpeakerTurn] = []
    for bioguide, parsed in members:
        header = re.compile(rf"^  {re.escape(parsed)}\.(?= )", re.M)
        for m in header.finditer(text):
            end = next((b for b in boundaries if b > m.start()), len(text))
            turn_text = text[m.start():end].rstrip()
            if turn_text not in text:  # the invariant this module exists to keep
                raise AssertionError(
                    f"extracted turn is not a verbatim substring (speaker {parsed})"
                )
            turns.append(SpeakerTurn(bioguide, parsed, turn_text, m.start()))
    turns.sort(key=lambda t: t.start)
    for i, t in enumerate(turns):
        t.turn_index = i
    return turns
