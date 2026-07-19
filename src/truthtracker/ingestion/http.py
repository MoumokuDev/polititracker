"""Shared HTTP plumbing: descriptive UA, 429 backoff, gentle pacing."""

import time

import httpx

from truthtracker.config import get_settings

UA = {"User-Agent": "TruthTracker/0.1 (self-hosted accountability research tool)"}


class RateLimitedClient:
    def __init__(
        self,
        base_params: dict | None = None,
        min_interval: float = 0.15,
        headers: dict | None = None,
    ):
        self._client = httpx.Client(
            timeout=90, follow_redirects=True, headers={**UA, **(headers or {})}
        )
        self._base = base_params or {}
        self._min_interval = min_interval
        self._last = 0.0
        self.request_count = 0

    def get(
        self, url: str, *, params_list: list[tuple[str, str]] | None = None, **params
    ) -> httpx.Response:
        """GET with pacing and 429 retry. Use params_list for repeated keys (fields[])."""
        merged = list({**self._base, **params}.items()) + list(params_list or [])
        for _attempt in range(4):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            resp = self._client.get(url, params=merged)
            self._last = time.monotonic()
            self.request_count += 1
            if resp.status_code == 429:
                delay = min(int(resp.headers.get("Retry-After", "30") or 30), 120)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"still rate-limited after retries: {url}")


def data_gov_client(min_interval: float = 0.75) -> RateLimitedClient:
    """Client for api.data.gov services (Congress.gov / GovInfo / FEC — shared key).

    Pass the interval matching the service's hourly budget: Congress.gov allows
    5,000/hr (0.75s); GovInfo and FEC allow 1,000/hr (3.6s for sustained
    backfills that must never trip the rolling window).
    """
    return RateLimitedClient({"api_key": get_settings().data_gov_api_key}, min_interval)


def plain_client() -> RateLimitedClient:
    """Client for keyless sources (senate.gov, GitHub raw)."""
    return RateLimitedClient()
