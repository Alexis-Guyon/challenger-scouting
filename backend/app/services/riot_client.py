"""
Riot API client with rate limiting.

Riot dev key default limits: 20 req / 1s, 100 req / 2min.
We implement a simple sliding-window limiter that respects both.
For prod-key scaling, swap with a Redis-backed token bucket.
"""
import asyncio
import time
from collections import deque
from typing import Any, Optional

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings


PLATFORM_HOSTS = {
    "br1": "br1.api.riotgames.com",
    "eun1": "eun1.api.riotgames.com",
    "euw1": "euw1.api.riotgames.com",
    "jp1": "jp1.api.riotgames.com",
    "kr": "kr.api.riotgames.com",
    "la1": "la1.api.riotgames.com",
    "la2": "la2.api.riotgames.com",
    "na1": "na1.api.riotgames.com",
    "oc1": "oc1.api.riotgames.com",
    "tr1": "tr1.api.riotgames.com",
    "ru": "ru.api.riotgames.com",
}

REGION_HOSTS = {
    "americas": "americas.api.riotgames.com",
    "asia": "asia.api.riotgames.com",
    "europe": "europe.api.riotgames.com",
    "sea": "sea.api.riotgames.com",
}


class RateLimiter:
    """Sliding window for two parallel limits."""

    def __init__(self, short_limit: int = 20, short_window: float = 1.0,
                 long_limit: int = 100, long_window: float = 120.0):
        self.short_limit = short_limit
        self.short_window = short_window
        self.long_limit = long_limit
        self.long_window = long_window
        self._short: deque[float] = deque()
        self._long: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._short and now - self._short[0] > self.short_window:
                    self._short.popleft()
                while self._long and now - self._long[0] > self.long_window:
                    self._long.popleft()

                if len(self._short) < self.short_limit and len(self._long) < self.long_limit:
                    self._short.append(now)
                    self._long.append(now)
                    return

                wait_short = self.short_window - (now - self._short[0]) if len(self._short) >= self.short_limit else 0
                wait_long = self.long_window - (now - self._long[0]) if len(self._long) >= self.long_limit else 0
                wait = max(wait_short, wait_long, 0.05)
                await asyncio.sleep(wait)


class RiotClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.riot_api_key
        self.platform = settings.platform
        self.region = settings.region
        self.limiter = RateLimiter()
        self._client = httpx.AsyncClient(timeout=20.0, headers={"X-Riot-Token": self.api_key})

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def _get(self, host: str, path: str, params: Optional[dict] = None) -> Any:
        await self.limiter.acquire()
        url = f"https://{host}{path}"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception_type((httpx.TransportError, RateLimitedError, ServerError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(url, params=params)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "5"))
                    await asyncio.sleep(retry_after)
                    raise RateLimitedError("429")
                if 500 <= resp.status_code < 600:
                    raise ServerError(f"{resp.status_code}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()

    # ---------- Platform-host endpoints ----------
    async def challenger_league(self, queue: str = "RANKED_SOLO_5x5") -> dict:
        host = PLATFORM_HOSTS[self.platform]
        return await self._get(host, f"/lol/league/v4/challengerleagues/by-queue/{queue}")

    async def grandmaster_league(self, queue: str = "RANKED_SOLO_5x5") -> dict:
        host = PLATFORM_HOSTS[self.platform]
        return await self._get(host, f"/lol/league/v4/grandmasterleagues/by-queue/{queue}")

    async def master_league(self, queue: str = "RANKED_SOLO_5x5") -> dict:
        host = PLATFORM_HOSTS[self.platform]
        return await self._get(host, f"/lol/league/v4/masterleagues/by-queue/{queue}")

    async def summoner_by_id(self, summoner_id: str) -> dict:
        host = PLATFORM_HOSTS[self.platform]
        return await self._get(host, f"/lol/summoner/v4/summoners/{summoner_id}")

    async def summoner_by_puuid(self, puuid: str) -> dict:
        host = PLATFORM_HOSTS[self.platform]
        return await self._get(host, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")

    # ---------- Region-host endpoints ----------
    async def account_by_puuid(self, puuid: str) -> dict | None:
        """Resolve gameName + tagLine via account-v1 (region-host)."""
        host = REGION_HOSTS[self.region]
        return await self._get(host, f"/riot/account/v1/accounts/by-puuid/{puuid}")

    async def match_ids(self, puuid: str, count: int = 30, queue: int = 420, start: int = 0) -> list[str]:
        host = REGION_HOSTS[self.region]
        params = {"count": count, "queue": queue, "start": start, "type": "ranked"}
        result = await self._get(host, f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params=params)
        return result or []

    async def match(self, match_id: str) -> dict:
        host = REGION_HOSTS[self.region]
        return await self._get(host, f"/lol/match/v5/matches/{match_id}")

    async def match_timeline(self, match_id: str) -> dict:
        host = REGION_HOSTS[self.region]
        return await self._get(host, f"/lol/match/v5/matches/{match_id}/timeline")


class RateLimitedError(Exception):
    pass


class ServerError(Exception):
    pass
