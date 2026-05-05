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
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models import Match
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
    """Notable kill / objective events. Limited to the most informative types.

    Includes `position: {x, y}` on CHAMPION_KILL events when available — the
    Summoner's Rift coordinate system runs 0-15000 on both axes; (0,0) is
    the bottom-left corner (blue base side). The frontend uses these to
    paint a kill-position minimap.
    """
    out = []
    for frame in timeline.get("info", {}).get("frames", []):
        for ev in frame.get("events", []):
            t = ev.get("type")
            ts = ev.get("timestamp", 0) // 1000  # ms → sec
            if t == "CHAMPION_KILL":
                killer = by_pid.get(ev.get("killerId"))
                victim = by_pid.get(ev.get("victimId"))
                assists = [by_pid.get(a, {}).get("summoner_name") for a in ev.get("assistingParticipantIds", [])]
                pos = ev.get("position") or {}
                out.append({
                    "type": "kill",
                    "ts": ts,
                    "killer": killer["summoner_name"] if killer else None,
                    "killer_champion": killer["champion"] if killer else None,
                    "victim": victim["summoner_name"] if victim else None,
                    "victim_champion": victim["champion"] if victim else None,
                    "assists": [a for a in assists if a],
                    "team_id": killer["team_id"] if killer else None,
                    "position": {"x": pos.get("x"), "y": pos.get("y")} if pos else None,
                })
            elif t == "ELITE_MONSTER_KILL":
                killer = by_pid.get(ev.get("killerId"))
                pos = ev.get("position") or {}
                out.append({
                    "type": "objective",
                    "subtype": ev.get("monsterType", "?").lower(),
                    "monster_subtype": ev.get("monsterSubType", "").lower() if ev.get("monsterSubType") else None,
                    "ts": ts,
                    "killer": killer["summoner_name"] if killer else None,
                    "killer_champion": killer["champion"] if killer else None,
                    "team_id": ev.get("killerTeamId") or (killer["team_id"] if killer else None),
                    "position": {"x": pos.get("x"), "y": pos.get("y")} if pos else None,
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


def _team_gold_diff(gold_curves: list[dict]) -> dict:
    """Sum per-team gold across participants, return blue-minus-red diff
    series + the per-team totals (for stacked area charts).

    Returns:
      { "minutes": [...], "blue_total": [...], "red_total": [...],
        "diff": [...]  # blue - red, positive = blue ahead }
    """
    if not gold_curves:
        return {"minutes": [], "blue_total": [], "red_total": [], "diff": []}
    minutes = gold_curves[0].get("minutes") or []
    blue_total = [0] * len(minutes)
    red_total = [0] * len(minutes)
    for gc in gold_curves:
        gold = gc.get("gold") or []
        team = gc.get("team_id")
        bucket = blue_total if team == 100 else red_total
        for i, g in enumerate(gold):
            if i < len(bucket):
                bucket[i] += int(g or 0)
    diff = [b - r for b, r in zip(blue_total, red_total)]
    return {"minutes": minutes, "blue_total": blue_total, "red_total": red_total, "diff": diff}


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
    # Prefer the first multi-key (RIOT_API_KEYS) over the single RIOT_API_KEY
    # — Personal Keys expire every 24h, while users typically rotate the
    # multi-key list more proactively.
    from ..services.ingestion import _resolve_keys
    keys = _resolve_keys()
    api_key = keys[0] if keys else None
    async with RiotClient(api_key=api_key) as client:
        try:
            match_data = await client.match(match_id)
            timeline = await client.match_timeline(match_id)
        except Exception as exc:
            raise HTTPException(502, f"Riot API failed: {exc}")

    by_pid = _participant_role_map(match_data)
    gold_curves = _extract_gold_curves(timeline, by_pid)
    payload = {
        "match_id": match_id,
        "duration_min": match_data.get("info", {}).get("gameDuration", 0) // 60,
        "patch": ".".join((match_data.get("info", {}).get("gameVersion") or "").split(".")[:2]) or None,
        "queue_id": match_data.get("info", {}).get("queueId"),
        "blue_win": any(t.get("teamId") == 100 and t.get("win") for t in match_data.get("info", {}).get("teams", [])),
        "participants": [
            {**info, "participant_id": pid}
            for pid, info in by_pid.items()
        ],
        "gold_curves": gold_curves,
        "team_gold_diff": _team_gold_diff(gold_curves),
        "events": _extract_events(timeline, by_pid),
    }
    _TIMELINE_CACHE[match_id] = (payload, now + _CACHE_TTL_SEC)
    return payload


# ---------- Replay / export helpers ----------

@router.get("/{match_id}/export")
async def match_export(
    match_id: str,
    db: Session = Depends(get_db),
):
    """
    Bundle match-v5 data + timeline data into a single downloadable JSON.

    Note: this is NOT the .rofl in-game replay file (Riot's public API never
    exposes those — only the LoL client itself can download .rofl, via its
    local LCU on the player's machine). What you get here is everything Riot
    offers programmatically: full match summary, per-participant stats, and
    the frame-by-frame timeline. Enough for offline scout review, statistical
    analysis, or piping into a 3rd-party visualizer.
    """
    if not db.get(Match, match_id):
        raise HTTPException(404, "match not in DB — ingest it first")

    async with RiotClient() as client:
        try:
            match_data = await client.match(match_id)
            timeline = await client.match_timeline(match_id)
        except Exception as exc:
            raise HTTPException(502, f"Riot API failed: {exc}")

    bundle = {
        "match_id": match_id,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": (
            "This is the Riot match-v5 data + timeline. Not a .rofl in-game "
            "replay file (those require the LoL client's LCU API). Use this "
            "for stat analysis or 3rd-party visualizers."
        ),
        "match": match_data,
        "timeline": timeline,
    }
    body = json.dumps(bundle, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="match_{match_id}.json"',
        },
    )


@router.get("/{match_id}/external-links")
def match_external_links(match_id: str, db: Session = Depends(get_db)):
    """
    Pre-built external URLs for a match.
    Frontend uses these to drop deep-links to op.gg / lolprofile / leagueofgraphs.
    """
    if not db.get(Match, match_id):
        raise HTTPException(404, "match not in DB")

    # Match IDs look like "EUW1_7842018193". Strip the platform prefix for
    # services that expect just the numeric id.
    parts = match_id.split("_", 1)
    region_prefix = parts[0].lower() if len(parts) == 2 else "euw1"
    numeric = parts[1] if len(parts) == 2 else match_id

    # op.gg uses lowercase server codes; map ours (euw1 → euw)
    OPGG_REGION = {"euw1": "euw", "eun1": "eune", "kr": "kr", "na1": "na",
                   "br1": "br", "jp1": "jp", "la1": "lan", "la2": "las",
                   "oc1": "oce", "tr1": "tr", "ru": "ru"}.get(region_prefix, region_prefix.replace("1", ""))

    return {
        "match_id": match_id,
        "links": {
            "opgg": f"https://www.op.gg/lol/match/{OPGG_REGION.upper()}/{match_id}",
            "leagueofgraphs": f"https://www.leagueofgraphs.com/match/{OPGG_REGION}/{numeric}",
            "lolpros": f"https://lolpros.gg/games/{match_id}",
            "blitz": f"https://app.blitz.gg/lol/match/{match_id}",
        },
        "lol_client_instructions": (
            "Open the LoL client → 'Match History' → find this game by date "
            "or champion → click the download arrow (only available for games "
            "you played in or spectated)."
        ),
    }
