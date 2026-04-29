"""HTTPS client for Calix economic + earnings calendar.

Calix is a Cloudflare Worker at ``https://calix.fintrixmarkets.com``. Routes:

  GET /v1/calendar/economic/upcoming?currencies=USD,EUR&impact=High&limit=10
  GET /v1/calendar/earnings/upcoming?limit=20

Both return ``{updatedAt, source, stale, events|earnings}``. The ``stale``
boolean is what we propagate as a degraded-health signal — Calix self-reports
when its KV cache is past the freshness budget.

This module is consumed by ``checklist.py`` (skill 3) and the news-brief
skill (skill 4). It uses a small file-based 60s cache so repeat invocations
within a session don't hammer the worker. Tests inject an httpx ``MockTransport``
to avoid network access.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import httpx


DEFAULT_BASE_URL = "https://calix.fintrixmarkets.com"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_CACHE_DIR = Path.home() / ".cfd-skills" / "calix_cache"


class CalixUnavailable(RuntimeError):
    """Raised when Calix is unreachable or returns a non-2xx response."""


@dataclass(frozen=True)
class CalixResponse:
    """Wrapper that surfaces both the parsed payload and the staleness flag."""
    payload: dict[str, Any]
    stale: bool
    fetched_at_unix: float = field(default_factory=time.time)
    cached: bool = False

    @property
    def degraded(self) -> bool:
        return self.stale


def _cache_key(url: str, params: Mapping[str, Any]) -> str:
    canonical = url + "?" + "&".join(
        f"{k}={params[k]}" for k in sorted(params)
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _read_cache(cache_dir: Path, key: str, ttl_seconds: int) -> Optional[dict[str, Any]]:
    target = cache_dir / f"{key}.json"
    if not target.exists():
        return None
    age = time.time() - target.stat().st_mtime
    if age > ttl_seconds:
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(cache_dir: Path, key: str, payload: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{key}.json"
    target.write_text(json.dumps(payload), encoding="utf-8")


@dataclass
class CalixClient:
    """Minimal Calix wrapper — sync only (matches mt5-mcp's call style)."""

    base_url: str = DEFAULT_BASE_URL
    cache_dir: Path = DEFAULT_CACHE_DIR
    cache_seconds: int = 60
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: Optional[httpx.BaseTransport] = None  # tests inject MockTransport

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {"base_url": self.base_url, "timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.Client(**kwargs)

    def _get(self, path: str, params: Mapping[str, Any]) -> CalixResponse:
        key = _cache_key(path, params)
        cached = _read_cache(self.cache_dir, key, self.cache_seconds)
        if cached is not None:
            return CalixResponse(
                payload=cached,
                stale=bool(cached.get("stale", False)),
                cached=True,
            )
        try:
            with self._client() as client:
                resp = client.get(path, params=dict(params))
        except httpx.HTTPError as exc:
            raise CalixUnavailable(f"Calix request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CalixUnavailable(
                f"Calix returned {resp.status_code}: {resp.text[:200]}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CalixUnavailable(f"Calix returned non-JSON: {exc}") from exc
        _write_cache(self.cache_dir, key, payload)
        return CalixResponse(payload=payload, stale=bool(payload.get("stale", False)))

    def fetch_economic(
        self,
        *,
        currencies: Iterable[str] | str = "majors",
        impact: Iterable[str] = ("High",),
        limit: int = 10,
    ) -> CalixResponse:
        currencies_param: str
        if isinstance(currencies, str):
            currencies_param = currencies
        else:
            currencies_param = ",".join(currencies)
        return self._get(
            "/v1/calendar/economic/upcoming",
            {
                "currencies": currencies_param,
                "impact": ",".join(impact),
                "limit": str(limit),
            },
        )

    def fetch_earnings(self, *, limit: int = 20) -> CalixResponse:
        return self._get(
            "/v1/calendar/earnings/upcoming",
            {"limit": str(limit)},
        )


__all__ = [
    "CalixClient",
    "CalixResponse",
    "CalixUnavailable",
    "DEFAULT_BASE_URL",
]
