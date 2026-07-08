"""Fuzzy fallback for team names using rapidfuzz."""
from __future__ import annotations

from rapidfuzz import fuzz, process


def fuzzy_match_team(name: str, candidates: list[str] | set[str],
                     threshold: float = 85.0) -> tuple[str | None, float]:
    """Best token_sort_ratio match at or above threshold, else (None, score)."""
    if not candidates:
        return None, 0.0
    result = process.extractOne(name, list(candidates),
                                scorer=fuzz.token_sort_ratio)
    if result and result[1] >= threshold:
        return result[0], float(result[1])
    return None, float(result[1]) if result else 0.0
