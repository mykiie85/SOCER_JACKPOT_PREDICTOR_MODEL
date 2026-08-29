"""Team-name matching against one league's roster.

SportPesa spells clubs with corporate affixes and city suffixes that
football-data.co.uk drops ("Parma Calcio" vs "Parma", "KAA Gent" vs "Gent",
"Olympique Lyon" vs "Lyon"). A plain ``token_sort_ratio`` — which compares the
*whole* sorted string — scores these in the 40-80 range and rejected 30 of 31
real cases, so almost every covered fixture silently fell through to the odds
fallback.

The matcher below works in four escalating steps, all scoped to a single
league's roster:

1. exact spelling
2. exact after dropping club-type affixes ("FC Utrecht" -> "utrecht")
3. longest *leading* token run that exactly names a club — this is what keeps
   "Sampdoria Genoa" on Sampdoria and "Wisla Krakow" on Wisla rather than
   letting a token-set scorer grab the trailing city, which is a different
   club in both leagues. A leading run that drops tokens loses to a candidate
   that accounts for MORE of the raw name, so "Independiente Rivadavia" lands
   on "Ind. Rivadavia" and not on "Independiente" — a different club in the
   same league
4. rapidfuzz WRatio on the affix-stripped forms, gated by an ambiguity margin
   so a near-tie is reported unresolved instead of guessed

Wrong is worse than unknown here: an unresolved fixture falls back to market
odds, but a mis-resolved one prices Sampdoria's match off Genoa's form.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

# Club-type, legal and generic-descriptor tokens that carry no identity.
# Safe to drop because matching is always scoped to one league's roster.
_AFFIX = {
    "fc", "afc", "cf", "cfc", "sc", "sk", "sv", "sd", "cd", "ud", "ca", "ac",
    "as", "ss", "ssc", "us", "usd", "ssd", "rc", "rcd", "kv", "kvc", "kaa",
    "kfc", "rks", "ks", "gks", "mks", "bk", "if", "ff", "fk", "nk", "hk",
    "tsv", "vfl", "vfb", "fsv", "spvgg", "bsc", "ol", "srl", "spa", "gmbh",
    "ev", "club", "clube", "calcio", "futebol", "futbol", "fussball",
    "football", "sportif", "faaliyetler", "kulubu", "sportclub", "olympique",
    "asociacion", "de", "da", "do", "of", "the", "and",
}

_TIDY = re.compile(r"[^a-z0-9]+")


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(s: str) -> list[str]:
    return [t for t in _TIDY.sub(" ", _strip_accents(s).lower()).split() if t]


def core_tokens(s: str) -> list[str]:
    """Identity tokens: affixes removed, but never reduced to nothing."""
    t = tokens(s)
    return [x for x in t if x not in _AFFIX] or t


def normalize(s: str) -> str:
    return " ".join(core_tokens(s))


def _index(candidates, counts: dict | None):
    """normalized form -> single canonical spelling.

    EdgeBot's history carries duplicate spellings that differ only by trailing
    whitespace ("Utrecht" and "Utrecht "). Collapsing them by frequency keeps
    the canonical one instead of treating the pair as a genuine ambiguity.
    """
    groups: dict[str, list[str]] = {}
    for c in candidates:
        groups.setdefault(normalize(c), []).append(c)
    out = {}
    for k, v in groups.items():
        if len(v) == 1:
            out[k] = v[0]
        elif counts:
            out[k] = max(v, key=lambda x: counts.get(x, 0))
        else:
            out[k] = min(v, key=len)
    return out


def _covered(cand_norm: str, raw_tokens: list[str]) -> int:
    """How many of the raw name's identity tokens a candidate accounts for.

    Abbreviations count: football-data writes "Ind. Rivadavia" and "Atl.
    Tucuman", so a candidate token that is a leading fragment of a raw token
    ("ind" of "independiente") covers it.
    """
    ct = cand_norm.split()
    return sum(1 for r in raw_tokens
               if any(c == r or (len(c) >= 3 and r.startswith(c)) for c in ct))


def match_team(raw: str, candidates, threshold: float = 88.0,
               ambiguity_margin: float = 6.0,
               counts: dict | None = None) -> tuple[str | None, str, float]:
    """Best match for ``raw`` in one league's roster.

    Returns ``(canonical_name_or_None, method, score)``. ``method`` is one of
    exact / normalized / prefix[n] / fuzzy / ambiguous / unresolved and is kept
    for the admin digest so a bad mapping is traceable.
    """
    cands = list(candidates)
    if not cands:
        return None, "no-candidates", 0.0

    if raw in cands:
        return raw, "exact", 100.0

    by_norm = _index(cands, counts)

    n = normalize(raw)
    if n in by_norm:
        return by_norm[n], "normalized", 100.0

    # Leading-token preference — longest first, so a two-word club beats the
    # one-word prefix it starts with.
    rt = core_tokens(raw)
    for take in range(len(rt), 0, -1):
        pref = " ".join(rt[:take])
        if pref not in by_norm:
            continue
        if take < len(rt):
            # The prefix ignores the rest of the raw name, which is right when
            # the tail is a city ("Sampdoria Genoa") and wrong when the tail is
            # what distinguishes two clubs ("Independiente Rivadavia" is not
            # "Independiente"). Only a candidate that accounts for strictly
            # more of the raw name overrides it, and only if it is the only one.
            fuller = [k for k in by_norm if _covered(k, rt) > take]
            if len(fuller) == 1:
                return by_norm[fuller[0]], f"prefix-extended[{take}]", 100.0
            if len(fuller) > 1:
                return None, f"ambiguous({fuller[0]}~{fuller[1]})", 0.0
        return by_norm[pref], f"prefix[{take}]", 100.0

    scored = process.extract(n, list(by_norm), scorer=fuzz.WRatio, limit=2)
    if not scored:
        return None, "unresolved", 0.0
    best, best_score = scored[0][0], scored[0][1]
    second = scored[1][1] if len(scored) > 1 else 0.0
    if best_score < threshold:
        return None, "unresolved", float(best_score)
    if best_score - second < ambiguity_margin:
        return None, f"ambiguous({best}~{scored[1][0]})", float(best_score)
    return by_norm[best], "fuzzy", float(best_score)


def fuzzy_match_team(name: str, candidates: list[str] | set[str],
                     threshold: float = 88.0) -> tuple[str | None, float]:
    """Back-compat wrapper used by the resolver tests."""
    hit, _method, score = match_team(name, candidates, threshold=threshold)
    return hit, score
