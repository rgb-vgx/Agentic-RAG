"""Sparse vector encoding for the sparse retrieval channel (BM25-style).

Produces Qdrant SparseVector-compatible (indices, values) pairs. Uses a
simple bag-of-words weighting; swap in a trained SPLADE model behind the
same interface when offline benchmarks justify the cost.
"""

import math
import re
from collections import Counter


class SparseEncoder:
    """Deterministic lexical encoder — no model weights required."""

    _TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

    def encode(self, text: str) -> tuple[list[int], list[float]]:
        tokens = [t.lower() for t in self._TOKEN_RE.findall(text)]
        if not tokens:
            return [], []

        term_freq = Counter(tokens)
        total = len(tokens)
        indices: list[int] = []
        values: list[float] = []
        for token, freq in term_freq.items():
            weight = 1.0 + math.log(freq)
            # Hash token → stable vocabulary index
            index = zlib_crc32(token) % (2**31)
            indices.append(index)
            values.append(weight / total * 1000)  # scale for float32 precision
        return indices, values


def zlib_crc32(token: str) -> int:
    import zlib

    return zlib.crc32(token.encode("utf-8"))
