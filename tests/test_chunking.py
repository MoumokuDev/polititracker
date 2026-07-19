from pathlib import Path

from truthtracker.search.chunking import MAX_CHARS, chunk_text

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "crec_house_sample.txt").read_text(encoding="utf-8")


def test_chunks_reassemble_exactly():
    for text in (SAMPLE, "short one", "  Para one.\n  Para two.\n"):
        assert "".join(chunk_text(text)) == text


def test_chunks_respect_max_size():
    long_text = SAMPLE * 20
    chunks = chunk_text(long_text)
    assert all(len(c) <= MAX_CHARS for c in chunks)
    assert "".join(chunks) == long_text


def test_single_giant_paragraph_is_split():
    text = "word " * 2000  # one giant no-paragraph blob
    chunks = chunk_text(text.strip() + ".")
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHARS for c in chunks)


def test_empty_text():
    assert chunk_text("") == []
