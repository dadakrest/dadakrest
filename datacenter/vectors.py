"""Local embedding and similarity helpers.

The data center ships with a deterministic hashing embedder so semantic search
works out of the box with no network access and no third-party packages. It is
a stand-in, not a language model: swap `embed` for a call to a real
embedding model when you want production-quality retrieval, keeping the same
return shape (a sparse, L2-normalized map of bucket to weight).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata

from .config import BUCKET_SPACE

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


def _bucket(token: str, space: int = BUCKET_SPACE) -> int:
    """Which bucket a token falls in. The digest is used whole, so two
    different words colliding is a ~2**-63 event."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % space


def embed(text: str, space: int = BUCKET_SPACE) -> dict[int, float]:
    """Return an L2-normalized bag-of-words embedding as a sparse bucket map.

    Sparse rather than a dense list: the bucket space is far too large to
    materialize, and a document only ever touches as many buckets as it has
    distinct words. Deterministic across processes and machines, so stored
    vectors stay comparable between runs. Term counts are damped with
    `1 + log(count)`, so a word repeated ten times does not outweigh ten
    distinct words.
    """
    if space <= 0:
        raise ValueError("space must be positive")

    counts: dict[int, float] = {}
    for token in tokenize(text):
        bucket = _bucket(token, space)
        counts[bucket] = counts.get(bucket, 0.0) + 1.0

    weights = {bucket: 1.0 + math.log(count) for bucket, count in counts.items()}
    norm = math.sqrt(sum(weight * weight for weight in weights.values()))
    if norm == 0.0:
        return {}
    return {bucket: weight / norm for bucket, weight in weights.items()}


def similarity(left: dict[int, float], right: dict[int, float]) -> float:
    """Cosine similarity of two embeddings from `embed`.

    Both sides are unit-norm by construction, so this is the dot product over
    the buckets they share, and iterating the smaller side keeps it cheap.
    """
    if not left or not right:
        return 0.0
    if len(right) < len(left):
        left, right = right, left
    return sum(weight * right[bucket] for bucket, weight in left.items() if bucket in right)


def dumps(vector: dict[int, float]) -> str:
    """Serialize an embedding for storage in the embeddings database."""
    values = {str(bucket): round(weight, 6) for bucket, weight in sorted(vector.items())}
    return json.dumps({"space": BUCKET_SPACE, "values": values})


def loads(blob: str) -> dict[int, float]:
    """Deserialize an embedding read back from the embeddings database."""
    payload = json.loads(blob)
    return {int(bucket): float(weight) for bucket, weight in payload["values"].items()}
