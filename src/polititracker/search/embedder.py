"""Local embedding model — no cloud dependency, per project principles.

bge-small-en-v1.5, 384-dim, cosine. The model is lazy-loaded once per process
(the API loads it on first search; the indexer on first batch). bge models
expect a retrieval instruction prefix on QUERIES ONLY; passages are embedded
bare.
"""

from functools import lru_cache

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer  # heavy import, deferred

    return SentenceTransformer(MODEL_NAME)


def embed_passages(texts: list[str]) -> list[list[float]]:
    return [
        v.tolist()
        for v in _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    ]


def embed_query(text: str) -> list[float]:
    return (
        _model()
        .encode([QUERY_PREFIX + text], normalize_embeddings=True, show_progress_bar=False)[0]
        .tolist()
    )
