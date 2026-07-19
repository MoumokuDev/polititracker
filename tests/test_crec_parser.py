"""CREC parser tests against a captured (authentic, trimmed) granule fixture."""

from pathlib import Path

from polititracker.ingestion.crec_parser import (
    SPEAKER_BOUNDARY,
    extract_turns,
    granule_plain_text,
    parse_mods_granules,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "crec_house_sample.txt").read_text(encoding="utf-8")


def test_granule_plain_text_strips_markup():
    htm = "<html><body><pre>\nA &amp; B <a href='x'>link</a> C\n</pre></body></html>"
    assert granule_plain_text(htm) == "\nA & B link C\n"


def test_boundaries_found_in_sample():
    matches = SPEAKER_BOUNDARY.findall(SAMPLE)
    assert len(SPEAKER_BOUNDARY.findall(SAMPLE)) >= 2, matches


def test_extract_bost_turn():
    turns = extract_turns(SAMPLE, [("B001295", "Mr. BOST")])
    assert len(turns) >= 1
    turn = turns[0]
    assert turn.bioguide == "B001295"
    assert turn.text.startswith("  Mr. BOST.")
    assert "pursuant to House Resolution 1423" in turn.text
    # the turn ends where the next speaker begins
    assert "The SPEAKER pro tempore." not in turn.text


def test_turn_is_verbatim_substring():
    for turn in extract_turns(SAMPLE, [("B001295", "Mr. BOST")]):
        assert turn.text in SAMPLE


def test_unmatched_member_yields_no_turns():
    # bare-surname parsed forms (vote listings) never match speech headers
    assert extract_turns(SAMPLE, [("A000370", "Adams")]) == []


def test_parse_mods_granules_minimal():
    mods = """<?xml version="1.0"?>
    <mods xmlns="http://www.loc.gov/mods/v3" xmlns:xlink="http://www.w3.org/1999/xlink">
      <relatedItem type="constituent" ID="id-CREC-2026-07-16-pt1-PgH1-2">
        <extension>
          <searchTitle>EXAMPLE; Congressional Record</searchTitle>
          <granuleClass>HOUSE</granuleClass>
          <granuleDate>2026-07-16</granuleDate>
          <congMember bioGuideId="B001295" chamber="H" congress="119" role="SPEAKING">
            <name type="parsed">Mr. BOST</name>
            <name type="authority-fnf">Mike Bost</name>
          </congMember>
        </extension>
      </relatedItem>
    </mods>"""
    granules = parse_mods_granules(mods)
    assert len(granules) == 1
    g = granules[0]
    assert g.granule_id == "CREC-2026-07-16-pt1-PgH1-2"
    assert g.granule_class == "HOUSE"
    assert g.date == "2026-07-16"
    assert g.members == [("B001295", "Mr. BOST")]
