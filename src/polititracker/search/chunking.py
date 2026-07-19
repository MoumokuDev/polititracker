"""Statement chunking for retrieval.

Chunks are CONTIGUOUS slices of the original utterance: concatenating a
statement's chunks in order reproduces the utterance text exactly. That keeps
the verbatim-substring invariant trivially true for everything the search
layer ever displays, and it is unit-tested as a hard property.
"""

import re

TARGET_CHARS = 900
MAX_CHARS = 1400

# a CREC paragraph begins at a newline followed by exactly-two-space indent
_PARA_START = re.compile(r"\n(?=  \S)")


def _segments(text: str) -> list[tuple[int, int]]:
    """Contiguous [start, end) paragraph spans covering the whole text."""
    starts = [0] + [m.start() for m in _PARA_START.finditer(text)]
    starts = sorted(set(starts))
    return [(s, starts[i + 1] if i + 1 < len(starts) else len(text)) for i, s in enumerate(starts)]


def _hard_split(start: int, end: int, text: str) -> list[tuple[int, int]]:
    """Split an oversized span at sentence-ish boundaries; slices stay contiguous."""
    spans = []
    pos = start
    while end - pos > MAX_CHARS:
        window = text[pos : pos + MAX_CHARS]
        cut = window.rfind(". ")
        if cut < TARGET_CHARS // 2:  # no usable sentence break; fall back to a word break
            cut = window.rfind(" ")
        if cut <= 0:
            cut = MAX_CHARS - 1
        spans.append((pos, pos + cut + 1))
        pos = pos + cut + 1
    spans.append((pos, end))
    return spans


def chunk_text(text: str) -> list[str]:
    """Split text into ordered chunks that join back to exactly the input."""
    if not text:
        return []
    pieces: list[tuple[int, int]] = []
    for seg_start, seg_end in _segments(text):
        if seg_end - seg_start > MAX_CHARS:
            pieces.extend(_hard_split(seg_start, seg_end, text))
        else:
            pieces.append((seg_start, seg_end))

    chunks: list[str] = []
    cur_start, cur_end = pieces[0]
    for start, end in pieces[1:]:
        if (end - cur_start) <= MAX_CHARS and (cur_end - cur_start) < TARGET_CHARS:
            cur_end = end
        else:
            chunks.append(text[cur_start:cur_end])
            cur_start, cur_end = start, end
    chunks.append(text[cur_start:cur_end])

    assert "".join(chunks) == text, "chunks must reassemble to the exact original text"
    return chunks
