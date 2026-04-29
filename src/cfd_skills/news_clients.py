"""Per-provider news clients (Finnhub / Marketaux / ForexNews API).

Each client normalises its provider-specific shape into a common
``NewsArticle`` dataclass (defined in ``news_dedup``). All three accept an
injected httpx transport for tests, share a 60s on-disk cache, and return
gracefully (empty list + status string) when:

  - the API key env var is unset (``"no_api_key"``)
  - the upstream returns 4xx/5xx (``"http_<code>"``)
  - the upstream times out or DNS fails (``"unavailable"``)
  - the JSON shape doesn't match the expected schema (``"schema_error"``)

The orchestrator uses the per-provider status to populate the brief's
"health" section so the user sees which providers contributed.

API keys never live in code or config — only env vars. The env var name
is configurable per-instance so a user can rotate to a different key
namespace without touching code.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import httpx

from cfd_skills.news_dedup import NewsArticle, canonicalise_url, classify_impact


DEFAULT_NEWS_CACHE_DIR = Path.home() / ".cfd-skills" / "news_cache"
DEFAULT_TIMEOUT_SECONDS = 8.0


# ---------- Cache helpers --------------------------------------------------


def _cache_key(provider: str, params: Mapping[str, Any]) -> str:
    canonical = provider + "?" + "&".join(
        f"{k}={params[k]}" for k in sorted(params)
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _read_cache(cache_dir: Path, key: str, ttl_seconds: int) -> Optional[Any]:
    target = cache_dir / f"{key}.json"
    if not target.exists():
        return None
    if time.time() - target.stat().st_mtime > ttl_seconds:
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(cache_dir: Path, key: str, payload: Any) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


# ---------- Provider base --------------------------------------------------


@dataclass
class NewsClientBase:
    name: str
    api_key_env_var: str
    base_url: str
    cache_dir: Path = DEFAULT_NEWS_CACHE_DIR
    cache_seconds: int = 60
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: Optional[httpx.BaseTransport] = None
    api_key: Optional[str] = None  # explicit override (mostly for tests)

    def _resolve_key(self) -> Optional[str]:
        if self.api_key is not None:
            return self.api_key
        return os.environ.get(self.api_key_env_var)

    def _client(self) -> httpx.Client:
        kw: dict[str, Any] = {"base_url": self.base_url, "timeout": self.timeout}
        if self.transport is not None:
            kw["transport"] = self.transport
        return httpx.Client(**kw)

    def _http_get(
        self, path: str, params: Mapping[str, Any]
    ) -> tuple[Optional[Any], str]:
        """Returns (json_payload, status). Status is one of the names
        documented in the module docstring."""
        try:
            with self._client() as client:
                resp = client.get(path, params=dict(params))
        except httpx.HTTPError:
            return None, "unavailable"
        if resp.status_code >= 400:
            return None, f"http_{resp.status_code}"
        try:
            return resp.json(), "ok"
        except ValueError:
            return None, "schema_error"


# ---------- Finnhub --------------------------------------------------------


@dataclass
class FinnhubClient(NewsClientBase):
    """Finnhub general news / company-news. Endpoint: https://finnhub.io/api/v1"""
    name: str = "finnhub"
    api_key_env_var: str = "FINNHUB_API_KEY"
    base_url: str = "https://finnhub.io/api/v1"

    def fetch_general(
        self, *, lookback_hours: int = 12, limit: int = 25
    ) -> tuple[list[NewsArticle], str]:
        key = self._resolve_key()
        if not key:
            return [], "no_api_key"
        cache_id = _cache_key(self.name, {"general": "1", "h": lookback_hours, "n": limit})
        cached = _read_cache(self.cache_dir, cache_id, self.cache_seconds)
        if cached is not None:
            return [_finnhub_to_article(b) for b in cached], "cache"

        payload, status = self._http_get(
            "/news", {"category": "general", "token": key}
        )
        if status != "ok" or not isinstance(payload, list):
            return [], status if status != "ok" else "schema_error"

        cutoff_ts = (
            datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        ).timestamp()
        filtered = [
            b for b in payload
            if isinstance(b, dict) and b.get("datetime", 0) >= cutoff_ts
        ][:limit]
        _write_cache(self.cache_dir, cache_id, filtered)
        return [_finnhub_to_article(b) for b in filtered], "ok"


def _finnhub_to_article(blob: dict[str, Any]) -> NewsArticle:
    title = str(blob.get("headline", "")).strip()
    summary = str(blob.get("summary", "")).strip()
    url = str(blob.get("url", "")).strip()
    publisher = str(blob.get("source", "")).strip()
    related_raw = str(blob.get("related", "") or "")
    symbols = tuple(s.strip() for s in related_raw.split(",") if s.strip())
    epoch = blob.get("datetime") or 0
    published = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return NewsArticle(
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published,
        source="finnhub",
        publisher=publisher,
        symbols=symbols,
        keywords=(str(blob.get("category", "")).strip(),) if blob.get("category") else (),
        impact=classify_impact(title, summary),
    )


# ---------- Marketaux -----------------------------------------------------


@dataclass
class MarketauxClient(NewsClientBase):
    """Marketaux /v1/news/all. Endpoint: https://api.marketaux.com/v1"""
    name: str = "marketaux"
    api_key_env_var: str = "MARKETAUX_API_KEY"
    base_url: str = "https://api.marketaux.com/v1"

    def fetch(
        self,
        *,
        symbols: Iterable[str] = (),
        lookback_hours: int = 12,
        limit: int = 25,
    ) -> tuple[list[NewsArticle], str]:
        key = self._resolve_key()
        if not key:
            return [], "no_api_key"
        published_after = (
            datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        ).strftime("%Y-%m-%dT%H:%M")
        symbols_param = ",".join(sorted({s.upper() for s in symbols if s}))
        params: dict[str, Any] = {
            "api_token": key,
            "filter_entities": "true",
            "language": "en",
            "limit": str(min(limit, 50)),
            "published_after": published_after,
        }
        if symbols_param:
            params["symbols"] = symbols_param
        cache_id = _cache_key(
            self.name,
            {"s": symbols_param, "h": lookback_hours, "n": limit},
        )
        cached = _read_cache(self.cache_dir, cache_id, self.cache_seconds)
        if cached is not None:
            return [_marketaux_to_article(b) for b in cached], "cache"

        payload, status = self._http_get("/news/all", params)
        if status != "ok" or not isinstance(payload, dict):
            return [], status if status != "ok" else "schema_error"
        data = payload.get("data") or []
        if not isinstance(data, list):
            return [], "schema_error"
        _write_cache(self.cache_dir, cache_id, data)
        return [_marketaux_to_article(b) for b in data], "ok"


def _marketaux_to_article(blob: dict[str, Any]) -> NewsArticle:
    title = str(blob.get("title", "")).strip()
    summary = str(
        blob.get("description") or blob.get("snippet") or ""
    ).strip()
    url = str(blob.get("url", "")).strip()
    publisher = str(blob.get("source", "")).strip()
    raw_pub = blob.get("published_at")
    if raw_pub:
        try:
            published = datetime.fromisoformat(str(raw_pub).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except ValueError:
            published = datetime.now(timezone.utc)
    else:
        published = datetime.now(timezone.utc)
    entities = blob.get("entities") or []
    symbols: list[str] = []
    if isinstance(entities, list):
        for e in entities:
            if isinstance(e, dict):
                sym = e.get("symbol")
                if sym:
                    symbols.append(str(sym).upper())
    return NewsArticle(
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published,
        source="marketaux",
        publisher=publisher,
        symbols=tuple(dict.fromkeys(symbols)),
        keywords=(),
        impact=classify_impact(title, summary),
    )


# ---------- ForexNews API --------------------------------------------------


@dataclass
class ForexNewsClient(NewsClientBase):
    """forexnewsapi.com /api/v1?currencypair=...&items=...&token=..."""
    name: str = "forexnews"
    api_key_env_var: str = "FOREXNEWS_API_KEY"
    base_url: str = "https://forexnewsapi.com/api/v1"

    def fetch(
        self,
        *,
        currencypairs: Iterable[str] = (),
        currencies: Iterable[str] = (),
        limit: int = 25,
    ) -> tuple[list[NewsArticle], str]:
        key = self._resolve_key()
        if not key:
            return [], "no_api_key"
        cp = ",".join(sorted({c.upper() for c in currencypairs if c}))
        ccys = ",".join(sorted({c.upper() for c in currencies if c}))
        if not cp and not ccys:
            # ForexNews requires currencypair or currency — the bare base
            # endpoint without either returns an HTML error page (parsed as
            # schema_error). Skip cleanly with a no-fault status so the
            # orchestrator doesn't flag NEWS_PROVIDER_DEGRADED.
            return [], "no_query"
        params: dict[str, Any] = {"items": str(min(limit, 50)), "token": key}
        if cp:
            params["currencypair"] = cp
        else:
            params["currency"] = ccys
        cache_id = _cache_key(
            self.name, {"cp": cp, "ccys": ccys, "n": limit}
        )
        cached = _read_cache(self.cache_dir, cache_id, self.cache_seconds)
        if cached is not None:
            return [_forexnews_to_article(b) for b in cached], "cache"

        payload, status = self._http_get("", params)
        if status != "ok" or not isinstance(payload, dict):
            return [], status if status != "ok" else "schema_error"
        data = payload.get("data") or []
        if not isinstance(data, list):
            return [], "schema_error"
        _write_cache(self.cache_dir, cache_id, data)
        return [_forexnews_to_article(b) for b in data], "ok"


def _forexnews_to_article(blob: dict[str, Any]) -> NewsArticle:
    title = str(blob.get("title", "")).strip()
    summary = str(blob.get("text", "")).strip()
    url = str(blob.get("news_url", "")).strip()
    publisher = str(blob.get("source_name", "")).strip()
    raw_pub = blob.get("date")
    if raw_pub:
        try:
            published = datetime.fromisoformat(
                str(raw_pub).replace("Z", "+00:00")
            )
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except ValueError:
            published = datetime.now(timezone.utc)
    else:
        published = datetime.now(timezone.utc)
    currencies_raw = blob.get("currency") or []
    if isinstance(currencies_raw, str):
        currencies_raw = [currencies_raw]
    keywords = tuple(str(k).upper() for k in currencies_raw)
    return NewsArticle(
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonicalise_url(url),
        published_at_utc=published,
        source="forexnews",
        publisher=publisher,
        symbols=keywords,  # ForexNews returns currency tags, not tickers
        keywords=keywords,
        impact=classify_impact(title, summary),
    )


__all__ = [
    "FinnhubClient",
    "MarketauxClient",
    "ForexNewsClient",
]
