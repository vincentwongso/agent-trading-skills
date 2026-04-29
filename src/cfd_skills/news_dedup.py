"""News dedup, URL canonicalisation, and keyword-based impact classification.

When the same story is published on Reuters, syndicated to Yahoo, then picked
up by Marketaux, the brief should show one entry with three sources rather
than three rows of duplicates.

Two-stage dedup:
  1. Canonicalise URL (strip tracking params, normalise host case) and group
     identical canonical URLs first.
  2. Within remaining articles, cluster by headline similarity using a
     normalised Levenshtein ratio (1.0 = identical). Threshold default 0.85
     per the plan.

Impact classifier is keyword-based, since the plan calls for a deterministic
mapping rather than ML. The agent can override at render time if it wants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# ---------- NewsArticle ----------------------------------------------------


@dataclass(frozen=True)
class NewsArticle:
    title: str
    summary: str
    url: str
    canonical_url: str
    published_at_utc: datetime
    source: str          # "finnhub" / "marketaux" / "forexnews"
    publisher: str       # "Reuters", "Bloomberg", etc.
    symbols: tuple[str, ...]   # broker-format if mappable, else raw tickers
    keywords: tuple[str, ...]
    impact: str          # "high" / "medium" / "low"


@dataclass(frozen=True)
class ClusteredArticle:
    primary: NewsArticle
    duplicates: tuple[NewsArticle, ...] = ()

    @property
    def all_sources(self) -> tuple[str, ...]:
        seen: list[str] = []
        for a in (self.primary, *self.duplicates):
            label = f"{a.source}/{a.publisher}" if a.publisher else a.source
            if label not in seen:
                seen.append(label)
        return tuple(seen)

    @property
    def all_articles(self) -> tuple[NewsArticle, ...]:
        return (self.primary, *self.duplicates)


# ---------- URL canonicalisation -------------------------------------------


_TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "_hsenc", "_hsmi")


def _strip_tracking(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=False)
    kept = [
        (k, v)
        for k, v in pairs
        if not any(k.lower().startswith(p) for p in _TRACKING_PARAM_PREFIXES)
    ]
    return urlencode(kept)


def canonicalise_url(url: str) -> str:
    """Lowercase host, strip tracking params + fragment + trailing slash."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    if not parsed.netloc:
        return url.strip().lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = _strip_tracking(parsed.query)
    return urlunparse((
        parsed.scheme.lower() or "https",
        netloc,
        path,
        "",            # params
        query,
        "",            # fragment
    ))


# ---------- Levenshtein ----------------------------------------------------


def _normalise_headline(s: str) -> str:
    """Lowercase, collapse whitespace, drop non-alphanumeric punctuation."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def levenshtein(a: str, b: str) -> int:
    """Plain Levenshtein edit distance (iterative two-row implementation)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    cur = [0] * (len(b) + 1)
    for i, ac in enumerate(a, start=1):
        cur[0] = i
        for j, bc in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ac == bc else 1)
            cur[j] = min(ins, dele, sub)
        prev, cur = cur, prev
    return prev[len(b)]


def levenshtein_ratio(a: str, b: str) -> float:
    """1.0 = identical (after normalisation), 0.0 = nothing in common.

    Operates on the *normalised* form of the inputs, so capitalisation and
    punctuation differences don't cost similarity.
    """
    na = _normalise_headline(a)
    nb = _normalise_headline(b)
    if not na and not nb:
        return 1.0
    longest = max(len(na), len(nb))
    if longest == 0:
        return 0.0
    distance = levenshtein(na, nb)
    return 1.0 - distance / longest


# ---------- Clustering -----------------------------------------------------


def dedupe_articles(
    articles: Iterable[NewsArticle],
    *,
    similarity_threshold: float = 0.85,
) -> list[ClusteredArticle]:
    """Cluster identical canonical URLs first, then near-duplicate headlines.

    The primary article in each cluster is the earliest-published one that
    has the longest summary — this stabilises ordering across runs and gives
    the user the most context.
    """
    items = list(articles)
    if not items:
        return []

    # Stage 1: bucket by canonical URL.
    by_url: dict[str, list[NewsArticle]] = {}
    no_url: list[NewsArticle] = []
    for a in items:
        if a.canonical_url:
            by_url.setdefault(a.canonical_url, []).append(a)
        else:
            no_url.append(a)

    pre_clusters: list[list[NewsArticle]] = list(by_url.values())
    pre_clusters.extend([[a] for a in no_url])

    # Stage 2: merge clusters whose primary headlines are above the threshold.
    merged: list[list[NewsArticle]] = []
    for cluster in pre_clusters:
        primary = _pick_primary(cluster)
        absorbed = False
        for existing in merged:
            existing_primary = _pick_primary(existing)
            if (
                levenshtein_ratio(primary.title, existing_primary.title)
                >= similarity_threshold
            ):
                existing.extend(cluster)
                absorbed = True
                break
        if not absorbed:
            merged.append(cluster)

    return [
        ClusteredArticle(
            primary=_pick_primary(c),
            duplicates=tuple(a for a in c if a is not _pick_primary(c)),
        )
        for c in merged
    ]


def _pick_primary(articles: list[NewsArticle]) -> NewsArticle:
    """Earliest-published article; tiebreak on summary length, then title."""
    return min(
        articles,
        key=lambda a: (a.published_at_utc, -len(a.summary or ""), a.title),
    )


# ---------- Impact classifier ---------------------------------------------


_HIGH_IMPACT_KEYWORDS = (
    # Central banks / rates
    "fomc", "fed", "federal reserve", "ecb", "boe", "boj", "rba", "snb",
    "rate decision", "interest rate", "rate hike", "rate cut", "powell",
    "lagarde", "rate statement",
    # Inflation / labour
    "cpi", "consumer price", "inflation report", "core inflation",
    "non-farm payroll", "nonfarm payroll", "nfp", "unemployment rate",
    # Growth
    "gdp", "retail sales",
    # Geopolitics
    "war", "invasion", "missile strike", "sanctions", "embargo", "opec+ cut",
    "strait of hormuz", "ceasefire",
)

_MEDIUM_IMPACT_KEYWORDS = (
    "earnings", "guidance", "revenue beat", "revenue miss", "eps beat",
    "eps miss", "crude inventories", "natural gas storage", "ppi",
    "producer prices", "ism", "pmi", "services pmi", "manufacturing pmi",
    "retail inventory",
)


def classify_impact(title: str, summary: str = "") -> str:
    """Return ``high`` / ``medium`` / ``low`` based on keyword match.

    Match is case-insensitive on the concatenated ``title + summary``. Any
    high-impact hit wins; otherwise medium-impact; otherwise low.
    """
    text = (title + " " + summary).lower()
    if any(kw in text for kw in _HIGH_IMPACT_KEYWORDS):
        return "high"
    if any(kw in text for kw in _MEDIUM_IMPACT_KEYWORDS):
        return "medium"
    return "low"


__all__ = [
    "NewsArticle",
    "ClusteredArticle",
    "canonicalise_url",
    "levenshtein",
    "levenshtein_ratio",
    "dedupe_articles",
    "classify_impact",
]
