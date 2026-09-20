"""Local embedding and similarity helpers.

The data center ships with a deterministic hashing embedder so semantic search
works out of the box with no network access and no third-party packages. It is
a stand-in, not a language model: swap `embed_text` for a call to a real
embedding model when you want production-quality retrieval, keeping the same
return shape (a list of floats of length `dimensions`).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata

from .config import EMBEDDING_DIM

LOCAL_MODEL_NAME = "local-hashing-v1"

#: Word characters minus the underscore. `\w` is Unicode-aware for `str`
#: patterns, so accented letters and non-Latin scripts stay inside their token
#: instead of being treated as separators.
_TOKEN_RE = re.compile(r"[^\W_]+")

#: Words carried by almost every document, so they say nothing about which one
#: a query means. Dropping them keeps scores driven by the distinctive terms,
#: which matters here because every term weighs the same (there is no IDF): a
#: question like "what about penetration testing" otherwise spends a third of
#: its weight on "about". The list is tuned for English. `no`, `nor` and `not`
#: are deliberately left out so a negation still changes what a query matches.
STOPWORDS = frozenset(
    """
    a about above after again all an and any are as at be been before
    between but by can could did do does down during each few for from had
    has have he her here him his how i if in into is it its just me more
    most my now of off on once only or other our out over own same she
    should so some such than that the their then there these they this
    through to too under until up very was we were what when where which
    who why will with would you your yourself
    """.split()
    # The apostrophe is a separator, so a possessive or a contraction leaves a
    # fragment behind ("client's" -> client, s; "isn't" -> isn, t). The word
    # itself still matches; the fragment is meaningless and would otherwise
    # carry full term weight, letting any two texts match on their apostrophes.
    + """
    s t d m ll re ve ain aren couldn didn doesn don hadn hasn haven isn
    shouldn wasn weren won wouldn
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase the text, split it into Unicode word tokens, drop stopwords.

    The text is NFKC-normalized first so the same word written in two
    equivalent forms (a composed `ü` and a `u` plus a combining diaeresis, or a
    full-width digit and its ASCII twin) lands in one bucket rather than two.
    Accents are not folded away, so `Zurich` and `Zürich` remain distinct.
    """
    normalized = unicodedata.normalize("NFKC", text.casefold())
    return [token for token in _TOKEN_RE.findall(normalized) if token not in STOPWORDS]


def _bucket(token: str, dimensions: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


def embed_text(text: str, dimensions: int = EMBEDDING_DIM) -> list[float]:
    """Return an L2-normalized bag-of-words hash embedding for `text`.

    Deterministic across processes and machines, which keeps the stored vectors
    comparable between runs. Term counts are damped with `1 + log(count)` so a
    word repeated ten times does not outweigh ten distinct words.

    Distinct tokens can still land in the same bucket, so unrelated text can
    score a little above zero; `DataCenter.search` applies a minimum score to
    keep those collisions out of the results.
    """
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")

    counts: dict[int, float] = {}
    for token in tokenize(text):
        bucket = _bucket(token, dimensions)
        counts[bucket] = counts.get(bucket, 0.0) + 1.0

    vector = [0.0] * dimensions
    for bucket, count in counts.items():
        vector[bucket] = 1.0 + math.log(count)

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity of two equal-length vectors, 0.0 for a zero vector."""
    if len(left) != len(right):
        raise ValueError(f"vector length mismatch: {len(left)} != {len(right)}")

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def dumps(vector: list[float]) -> str:
    """Serialize a vector for storage in the embeddings database.

    These vectors are overwhelmingly zeros, so only the non-zero buckets are
    written. A document of thirty words stores thirty numbers, not thousands.
    """
    values = {
        str(index): round(value, 6)
        for index, value in enumerate(vector)
        if value != 0.0
    }
    return json.dumps({"dim": len(vector), "values": values})


def loads(blob: str) -> list[float]:
    """Deserialize a vector read back from the embeddings database."""
    payload = json.loads(blob)
    vector = [0.0] * int(payload["dim"])
    for index, value in payload["values"].items():
        vector[int(index)] = float(value)
    return vector
