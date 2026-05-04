"""
Unofficial lolesports.com API client.

Endpoints used (publicly known, used by the official lolesports.com frontend):
  - https://esports-api.lolesports.com/persisted/gw/getLeagues
  - https://esports-api.lolesports.com/persisted/gw/getTournamentsForLeague
  - https://esports-api.lolesports.com/persisted/gw/getSchedule
  - https://esports-api.lolesports.com/persisted/gw/getEventDetails
  - https://feed.lolesports.com/livestats/v1/window/{gameId}
  - https://feed.lolesports.com/livestats/v1/details/{gameId}

The `x-api-key` header is the same one hard-coded into the lolesports.com
frontend bundle. It has been stable for years but Riot can rotate it.
This API is NOT officially documented by Riot — use at your own risk and
keep usage internal.
"""
import asyncio
import json as _json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ESPORTS_API = "https://esports-api.lolesports.com/persisted/gw"
LIVESTATS = "https://feed.lolesports.com/livestats/v1"
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
HL = "en-US"
USER_AGENT = "ChallengerScoutingBot/0.1 (internal)"
RATE_DELAY = 0.25  # ~4 req/s — conservative


class LolesportsNoData(Exception):
    """Raised when the API returns 200 but the body is empty / non-JSON.
    Treated by callers as 'no data for this game' (same as 404), not a
    pipeline error."""


class LolesportsClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={"x-api-key": API_KEY, "User-Agent": USER_AGENT},
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self): return self
    async def __aexit__(self, *_): await self.close()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        last_decode_preview = None
        for attempt in range(4):
            r = await self._client.get(url, params=params)
            if r.status_code == 429:
                wait = 2 * (attempt + 1)
                logger.warning("lolesports 429, sleeping %ds", wait)
                await asyncio.sleep(wait)
                continue
            if 500 <= r.status_code < 600:
                await asyncio.sleep(1 + attempt)
                continue
            r.raise_for_status()

            # 200 OK — but the body may still be non-JSON (CDN serving a
            # cached HTML error page, empty body for stale games, etc.).
            # Retry on decode failure with backoff: many of these clear up
            # within 1-2s as the CDN cache expires.
            text = r.text
            if not text.strip():
                # Truly empty body → treat as no data for this game.
                await asyncio.sleep(RATE_DELAY)
                raise LolesportsNoData(f"empty 200 body for {url}")
            try:
                payload = _json.loads(text)
                await asyncio.sleep(RATE_DELAY)
                return payload
            except _json.JSONDecodeError as exc:
                last_decode_preview = text[:200].replace("\n", " ")
                wait = 1 + attempt
                logger.warning(
                    "lolesports JSON decode failed (attempt %d/4) for %s: %s — body preview: %s",
                    attempt + 1, url, exc, last_decode_preview,
                )
                await asyncio.sleep(wait)
                continue
        # Exhausted retries on JSONDecodeError → treat as no-data
        # rather than crashing the whole league sync. The caller
        # categorizes this as a soft skip.
        if last_decode_preview is not None:
            raise LolesportsNoData(
                f"non-JSON 200 body after 4 retries for {url}: {last_decode_preview!r}"
            )
        raise RuntimeError(f"lolesports unreachable: {url}")

    # ----- esports-api -----
    async def get_leagues(self) -> list[dict]:
        data = await self._get(f"{ESPORTS_API}/getLeagues", {"hl": HL})
        return data.get("data", {}).get("leagues", [])

    async def get_tournaments_for_league(self, league_id: str) -> list[dict]:
        data = await self._get(f"{ESPORTS_API}/getTournamentsForLeague",
                                {"hl": HL, "leagueId": league_id})
        leagues = data.get("data", {}).get("leagues", [])
        out: list[dict] = []
        for l in leagues:
            for t in l.get("tournaments", []):
                t["_leagueId"] = l.get("id")
                out.append(t)
        return out

    async def get_schedule(self, league_id: str, page_token: Optional[str] = None) -> dict:
        params = {"hl": HL, "leagueId": league_id}
        if page_token:
            params["pageToken"] = page_token
        data = await self._get(f"{ESPORTS_API}/getSchedule", params)
        return data.get("data", {}).get("schedule", {})

    async def get_event_details(self, event_id: str) -> dict | None:
        data = await self._get(f"{ESPORTS_API}/getEventDetails", {"hl": HL, "id": event_id})
        ev = (data.get("data") or {}).get("event")
        return ev

    # ----- livestats feed -----
    async def get_window(self, game_id: str, starting_time: Optional[str] = None) -> dict | None:
        """
        Returns 10s-interval frame data. starting_time is ISO8601 (game start, rounded to 10s).
        If omitted, we fetch the latest available window.

        Returns None when:
          - HTTP 404: this game has no livestats data on the feed
          - LolesportsNoData: 200 with empty/non-JSON body (after 4 retries)
        Both cases mean "no frame data available for this game" — same handling.
        """
        params = {}
        if starting_time:
            params["startingTime"] = starting_time
        try:
            return await self._get(f"{LIVESTATS}/window/{game_id}", params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        except LolesportsNoData:
            return None

    async def get_details(self, game_id: str, starting_time: Optional[str] = None,
                          participant_ids: Optional[list[int]] = None) -> dict | None:
        params = {}
        if starting_time:
            params["startingTime"] = starting_time
        if participant_ids:
            params["participantIds"] = "_".join(str(p) for p in participant_ids)
        try:
            return await self._get(f"{LIVESTATS}/details/{game_id}", params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        except LolesportsNoData:
            return None


def round_to_10s_iso(dt: datetime) -> str:
    """ISO8601 with seconds rounded to nearest 10."""
    second = (dt.second // 10) * 10
    dt = dt.replace(second=second, microsecond=0, tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
