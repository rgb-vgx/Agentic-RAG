"""Sparse vector encoding for the sparse retrieval channel (BM25-style).

Produces Qdrant SparseVector-compatible (indices, values) pairs. Uses a
simple bag-of-words weighting; swap in a trained SPLADE model behind the
same interface when offline benchmarks justify the cost.

Vietnamese note: the encoder is Unicode-aware. Words keep their diacritics
("nghỉ", "phép") and an accent-stripped variant ("nghi", "phep") is indexed
too, so a query typed without accents still matches an accented document —
the ASCII-only regex previously reduced them to fragments like "ngh"/"ph".
"""

import math
import re
import unicodedata
from collections import Counter


class SparseEncoder:
    """Deterministic lexical encoder — no model weights required."""

    _TOKEN_RE = re.compile(r"\w+", re.UNICODE)

    def encode(self, text: str) -> tuple[list[int], list[float]]:
        tokens = self._tokenize(text)
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

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        """NFC + casefold, then each token plus its accent-stripped form."""
        normalized = unicodedata.normalize("NFC", text).casefold()
        tokens: list[str] = []
        for token in cls._TOKEN_RE.findall(normalized):
            tokens.append(token)
            stripped = strip_diacritics(token)
            if stripped and stripped != token:
                tokens.append(stripped)
        return tokens


def strip_diacritics(token: str) -> str:
    """Remove combining marks; "đ" is a distinct letter and needs its own map."""
    decomposed = unicodedata.normalize("NFD", token)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return without_marks.replace("đ", "d")


def zlib_crc32(token: str) -> int:
    import zlib

    return zlib.crc32(token.encode("utf-8"))
