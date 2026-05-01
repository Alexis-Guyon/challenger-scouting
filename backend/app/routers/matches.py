"""
Match deep-dive endpoint.

We don't store the full timeline in DB (too heavy), so this endpoint refetches
it from Riot on demand and parses out:
  - per-participant gold curve (1 sample per minute)
  - kill / objective events with timestamps
  - lane diffs at every minute (gold/xp/cs)

A small in-memory cache keeps recently-viewed matches warm so navigating back
and forth doesn't re-fetch.
"""
import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import MatchParticipant, Match
from ..services.riot_client import RiotClient

router = APIRouter(prefix="/matches", tags=["matches"], dependencies=[Depends(get_current_user)])


# match_id → (cached_payload, expiry_epoch)
_TIMELINE_CACHE: dict[str, tuple[dict, float]] = {}
_CACHE_TTL_SEC = 30 * 60  # 30 min — timeline data is immutable


def _participant_role_map(match_payload: dict) -> dict[int, dict]:
    out = {}
    for p in match_payload.get("info", {}).get("participants", []):
        out[p["participantId"]] = {
            "puuid": p.get("puuid"),
            "summoner_name": p.get("riotIdGameName") or p.get("summonerName"),
            "tagline": p.get("riotIdTagline"),
            "champion": p.get("championName"),
            "role": p.get("teamPosition"),
            "team_id": p.get("teamId"),
            "win": p.get("win"),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
        }
    return out


def _extract_gold_curves(timeline: dict, by_pid: dict[int, dict]) -> list[dict]:
    """Per-participant minute-by-minute gold curve."""
    frames = timeline.get("info", {}).get("frames", [])
    series: dict[int, list[int]] = {pid: [] for pid in by_pid}
    minutes: list[int] = []
    for i, frame in enumerate(frames):
        minutes.append(i)  # frames are 1-min apart in match-v5 timelines
        pf = frame.get("participantFrames", {})
        for pid in by_pid:
            data = pf.get(str(pid), {})
            series[pid].append(data.get("totalGold", 0))

    out = []
    for pid, golds in series.items():
        info = by_pid[pid]
        out.append({
            "participant_id": pid,
            "puuid": info["puuid"],
            "summoner_name": info["summoner_name"],
            "champion": info["champion"],
            "role": info["role"],
            "team_id": info["team_id"],
            "win": info["win"],
            "minutes": minutes,
            "gold": golds,
        })
    return out


def _extract_events(timeline: dict, by_pid: dict[int, dict]) -> list[dict]:
    """Notable kill / objective events. Limited to the most informative types."""
    out = []
    for frame in timeline.get("info", {}).get("frames", []):
        for ev in frame.get("events", []):
            t = ev.get("type")
            ts = ev.get("timestamp", 0) // 1000  # ms → sec
            if t == "CHAMPION_KILL":
                killer = by_pid.get(ev.get("killerId"))
                victim = by_pid.get(ev.get("victimId"))
                assists = [by_pid.get(a, {}).get("summoner_name") for a in ev.get("assistingParticipantIds", [])]
                out.append({
                    "type": "kill",
                    "ts": ts,
                    "killer": killer["summoner_name"] if killer else None,
                    "killer_champion": killer["champion"] if killer else None,
                    "victim": victim["summoner_name"] if victim else None,
                    "victim_champion": victim["champion"] if victim else None,
                    "assists": [a for a in assists if a],
                    "team_id": killer["team_id"] if killer else None,
                })
            elif t == "ELITE_MONSTER_KILL":
                killer = by_pid.get(ev.get("killerId"))
                out.append({
                    "type": "objective",
                    "subtype": ev.get("monsterType", "?").lower(),
                    "monster_subtype": ev.get("monsterSubType", "").lower() if ev.get("monsterSubType") else None,
                    "ts": ts,
                    "killer": killer["summoner_name"] if killer else None,
                    "killer_champion": killer["champion"] if killer else None,
                    "team_id": ev.get("killerTeamId") or (killer["team_id"] if killer else None),
                })
            elif t == "BUILDING_KILL":
                out.append({
                    "type": "tower",
                    "subtype": (ev.get("buildingType") or "").lower(),
                    "tower_type": (ev.get("towerType") or "").lower() if ev.get("towerType") else None,
                    "lane": (ev.get("laneType") or "").lower() if ev.get("laneType") else None,
                    "ts": ts,
                    "team_id": ev.get("teamId"),
                })
    return out


@router.get("/{match_id}/timeline")
async def match_timeline(
    match_id: str,
    db: Session = Depends(get_db),
):
    """
    Full match deep-dive: participant identities, gold curves, kill/objective
    events, summary. Cached 30 min in memory.
    """
    now = time.time()
    cached = _TIMELINE_CACHE.get(match_id)
    if cached and cached[1] > now:
        return cached[0]

    # Sanity: only fetch if the match is in our DB (avoid arbitrary fetches).
    if not db.get(Match, match_id):
        raise HTTPException(404, "match not in DB — ingest it first")

    # Riot match-v5 region routing happens via the RiotClient host config.
    async with RiotClient() as client:
        try:
            match_data = await client.match(match_id)
            timeline = await client.match_timeline(match_id)
        except Exception as exc:
            raise HTTPException(502, f"Riot API failed: {exc}")

    by_pid = _participant_role_map(match_data)
    payload = {
        "match_id": match_id,
        "duration_min": match_data.get("info", {}).get("gameDuration", 0) // 60,
        "patch": ".".join((match_data.get("info", {}).get("gameVersion") or "").split(".")[:2]) or None,
        "queue_id": match_data.get("info", {}).get("queueId"),
        "blue_win": any(t.get("teamId") == 100 and t.get("win") for t in match_data.get("info", {}).get("teams", [])),
        "participants": [
            {
                **info,
                "participant_id": pid,
            }
            for pid, info in by_pid.items()
        ],
        "gold_curves": _extract_gold_curves(timeline, by_pid),
        "events": _extract_events(timeline, by_pid),
    }
    _TIMELINE_CACHE[match_id] = (payload, now + _CACHE_TTL_SEC)
    return payload
