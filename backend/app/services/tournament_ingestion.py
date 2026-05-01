"""
Ingest pro tournament data from lolesports (unofficial).

Strategy:
1. Pull leagues, filter to EU-relevant ones (LEC + ERLs we care about).
2. For each league, pull schedule → list of events.
3. For each completed event, pull event details → list of games (gameIds).
4. For each game, pull livestats window (10s-interval frames) for GD/CSD/XPD@15.
5. For each game, pull livestats details for KDA, gold, damage, vision.
6. Persist OfficialMatch + OfficialMatchParticipant.
7. Update CurrentLECRoster from latest LEC games.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    CurrentLECRoster,
    OfficialMatch,
    OfficialMatchParticipant,
    ProTeam,
    Tournament,
)
from .lolesports_client import LolesportsClient, round_to_10s_iso

logger = logging.getLogger(__name__)


# Default leagues we care about for EU scouting. Slugs are stable on lolesports.
DEFAULT_LEAGUE_SLUGS: tuple[str, ...] = (
    "lec",            # LEC main
    "lfl",            # France
    "prime_league",   # DACH
    "superliga",      # Spain
    "nlc",            # NL/UK
    "hitpoint",       # Czech/Slovak
    "ebl",            # Balkans
    "ultraliga",      # Poland
    "elite_series",   # Benelux
    "esports_balkan_league",
    "lpl_cis",
    "tcl",            # Turkey
    "northern_league_of_legends_championship",
)


def _parse_iso(s: str | None) -> datetime | None:
    """Always returns timezone-aware UTC. Date-only strings are coerced to midnight UTC."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _normalize_role(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    return {
        "top": "top", "jungle": "jungle", "jng": "jungle",
        "mid": "mid", "middle": "mid",
        "adc": "bottom", "bot": "bottom", "bottom": "bottom",
        "sup": "support", "support": "support",
    }.get(s, s)


def _build_window_at_15min(window_data: dict) -> dict[int, dict]:
    """Pick the frame closest to 15:00 game time, return participant snapshots keyed by participantId."""
    frames = window_data.get("frames", []) if window_data else []
    if not frames:
        return {}
    target_sec = 15 * 60
    chosen = frames[-1]  # fallback to last frame if game ended early
    best_diff = float("inf")
    for f in frames:
        rfc = f.get("rfc460Timestamp")
        # Use frame index (10s steps) since rfc time is wall-clock
        # The frames feed has gameStateAge or we just count: each frame is 10s apart
        pass
    # Simpler: window frames come at fixed 10s intervals from gameStart
    # frames[i] = state at 10*i seconds. Index 90 = 15:00.
    if len(frames) > 90:
        chosen = frames[90]
    elif len(frames) > 0:
        # game shorter than 15min: use last frame (rare, surrender@15)
        chosen = frames[-1]

    out: dict[int, dict] = {}
    for side_key in ("blueTeam", "redTeam"):
        side = chosen.get(side_key) or {}
        for p in side.get("participants", []) or []:
            pid = p.get("participantId")
            if pid is None:
                continue
            out[pid] = {
                "totalGold": p.get("totalGold", 0),
                "level": p.get("level", 0),
                "kills": p.get("kills", 0),
                "deaths": p.get("deaths", 0),
                "assists": p.get("assists", 0),
                "creepScore": p.get("creepScore", 0),
                "currentHealth": p.get("currentHealth", 0),
                "maxHealth": p.get("maxHealth", 0),
            }
    return out


def _final_state(window_data: dict) -> dict:
    """Last frame of the window — match-final stats."""
    if not window_data:
        return {"by_pid": {}, "blue_win": None, "duration_sec": 0}
    frames = window_data.get("frames", [])
    if not frames:
        return {"by_pid": {}, "blue_win": None, "duration_sec": 0}
    last = frames[-1]
    by_pid: dict[int, dict] = {}
    for side_key, side_label in (("blueTeam", "blue"), ("redTeam", "red")):
        side = last.get(side_key) or {}
        for p in side.get("participants", []) or []:
            pid = p.get("participantId")
            if pid is None:
                continue
            p["_side"] = side_label
            by_pid[pid] = p
    return {
        "by_pid": by_pid,
        "duration_sec": len(frames) * 10,
        "blue_win": (last.get("blueTeam", {}).get("totalKills", 0) or 0)
                    > (last.get("redTeam", {}).get("totalKills", 0) or 0),  # rough fallback
    }


async def _ingest_one_game(client: LolesportsClient, db: Session,
                           game_id: str, event: dict, tournament_id: str) -> tuple[bool, str]:
    """Pull 1 game's frame data and persist. Returns (added, reason)."""
    if db.get(OfficialMatch, game_id):
        return False, "already_exists"

    # The livestats /window endpoint returns 10 frames (100 s) ending at the
    # requested startingTime. Without the parameter, you get the FIRST 10
    # frames of the game (all stats at 0). To grab the FINAL stats we request
    # a startingTime well past the broadcast start. If the API has data
    # beyond it, we get the last available window.
    bc_start = _parse_iso(event.get("startTime"))
    end_probe_iso = round_to_10s_iso(bc_start + timedelta(minutes=120)) if bc_start else None
    window = await client.get_window(game_id, end_probe_iso)
    if not window or not window.get("frames"):
        # Fall back to the no-arg call (start of game) — better than nothing
        window = await client.get_window(game_id)
    if not window or not window.get("frames"):
        return False, "no_window_data"

    last = _final_state(window)
    # The last window only spans 100 s; estimate full game duration from the
    # rfc460Timestamp of the last frame minus broadcast startTime (less ~3 min
    # for draft + load). Bounded to [15 min, 80 min] to filter outliers.
    duration_sec = last["duration_sec"]
    if window.get("frames") and bc_start:
        last_ts_str = window["frames"][-1].get("rfc460Timestamp")
        last_ts = _parse_iso(last_ts_str) if last_ts_str else None
        if last_ts:
            estimated = int((last_ts - bc_start).total_seconds() - 180)  # subtract draft+load
            if 15 * 60 <= estimated <= 80 * 60:
                duration_sec = estimated

    # Fetch a window centered on the 15:00 game-time mark for laning diffs.
    snap_15: dict[int, dict] = {}
    if bc_start:
        target_15 = bc_start + timedelta(minutes=18)  # +18 = ~3 min draft/loading + 15 in-game
        target_iso = round_to_10s_iso(target_15)
        try:
            w15 = await client.get_window(game_id, target_iso)
            if w15 and w15.get("frames"):
                # Pick the frame closest to the end of that window (≈15:00 in-game)
                f = w15["frames"][-1]
                for side_key in ("blueTeam", "redTeam"):
                    for p in (f.get(side_key) or {}).get("participants", []) or []:
                        pid = p.get("participantId")
                        if pid is not None:
                            snap_15[pid] = {
                                "totalGold": p.get("totalGold", 0),
                                "creepScore": p.get("creepScore", 0),
                                "level": p.get("level", 0),
                            }
        except Exception:
            pass

    # Details endpoint enriches with damage, vision, summoners, etc.
    details = await client.get_details(game_id, end_probe_iso)
    details_pid: dict[int, dict] = {}
    if details and details.get("frames"):
        last_d = details["frames"][-1]
        for side_key in ("blueTeam", "redTeam"):
            for p in (last_d.get(side_key) or {}).get("participants", []) or []:
                pid = p.get("participantId")
                if pid is not None:
                    details_pid[pid] = p

    # Determine teams + winner from the schedule event
    match_meta = (event.get("match") or {})
    teams = match_meta.get("teams", []) or []
    blue_team = teams[0] if teams else {}
    red_team = teams[1] if len(teams) > 1 else {}
    blue_team_id = blue_team.get("id")
    red_team_id = red_team.get("id")
    blue_win = None
    if blue_team.get("result", {}).get("outcome") == "win":
        blue_win = True
    elif red_team.get("result", {}).get("outcome") == "win":
        blue_win = False

    # Upsert ProTeam metadata (id only available via getEventDetails, hence here)
    for t in (blue_team, red_team):
        tid = t.get("id")
        if not tid:
            continue
        pt = db.get(ProTeam, tid)
        if not pt:
            pt = ProTeam(id=tid)
            db.add(pt)
        pt.code = t.get("code") or pt.code
        pt.name = t.get("name") or pt.name
        pt.image_url = t.get("image") or pt.image_url

    om = OfficialMatch(
        id=game_id,
        event_id=event.get("id"),
        tournament_id=tournament_id,
        block_name=event.get("blockName") or "",
        blue_team_id=blue_team_id,
        red_team_id=red_team_id,
        blue_win=blue_win,
        patch=window.get("gameMetadata", {}).get("patchVersion") or None,
        duration_sec=duration_sec,
        game_date=_parse_iso(event.get("startTime")),
        state="completed",
    )
    db.add(om)

    # Roster info comes from gameMetadata
    metadata = window.get("gameMetadata", {})
    blue_meta = metadata.get("blueTeamMetadata", {})
    red_meta = metadata.get("redTeamMetadata", {})

    team_kills_blue = sum(p.get("kills", 0) for p in
                          (window["frames"][-1].get("blueTeam") or {}).get("participants", []) or [])
    team_kills_red = sum(p.get("kills", 0) for p in
                         (window["frames"][-1].get("redTeam") or {}).get("participants", []) or [])

    for side_meta, side_label, team_id in (
        (blue_meta, "blue", blue_team_id),
        (red_meta, "red", red_team_id),
    ):
        for pinfo in side_meta.get("participantMetadata", []) or []:
            pid = pinfo.get("participantId")
            role = _normalize_role(pinfo.get("role"))
            esports_player_id = pinfo.get("esportsPlayerId")
            summoner_name = pinfo.get("summonerName") or ""
            champion = pinfo.get("championId") or ""
            # Display name fallback: prefer Leaguepedia later, here use summonerName
            player_name = summoner_name or (esports_player_id or "?")

            last_frame = last["by_pid"].get(pid, {})
            d = details_pid.get(pid, {})

            kills = last_frame.get("kills", 0)
            deaths = last_frame.get("deaths", 0)
            assists = last_frame.get("assists", 0)
            cs = last_frame.get("creepScore", 0)
            gold = last_frame.get("totalGold", 0)
            level = last_frame.get("level", 0)

            # Match opponent at same role for diffs
            opp_meta = (red_meta if side_label == "blue" else blue_meta)
            opp = next(
                (op for op in (opp_meta.get("participantMetadata") or [])
                 if _normalize_role(op.get("role")) == role),
                None
            )
            opp_pid = opp.get("participantId") if opp else None
            mine_15 = snap_15.get(pid, {})
            opp_15 = snap_15.get(opp_pid, {}) if opp_pid is not None else {}
            gd15 = mine_15.get("totalGold", 0) - opp_15.get("totalGold", 0) if opp_15 else 0
            csd15 = mine_15.get("creepScore", 0) - opp_15.get("creepScore", 0) if opp_15 else 0
            xpd15 = 0  # XP not directly exposed; left as 0 (window doesn't ship XP)

            tk = team_kills_blue if side_label == "blue" else team_kills_red
            kp = (kills + assists) / tk if tk else 0
            kda = (kills + assists) / max(deaths, 1)
            win = (side_label == "blue" and blue_win) or (side_label == "red" and blue_win is False)

            mp = OfficialMatchParticipant(
                match_id=game_id,
                team_id=team_id,
                side=side_label,
                role=role,
                pro_player_id=esports_player_id,
                player_name=player_name,
                summoner_name=summoner_name,
                champion=str(champion),
                win=win,
                kills=kills, deaths=deaths, assists=assists,
                cs=cs, gold=gold, level=level,
                gd_at_15=gd15, csd_at_15=csd15, xpd_at_15=xpd15,
                gold_at_15=mine_15.get("totalGold", 0),
                cs_at_15=mine_15.get("creepScore", 0),
                kda=kda, kill_participation=kp,
            )
            db.add(mp)

    db.commit()
    return True, "ok"


async def ingest_league(client: LolesportsClient, db: Session, league: dict, max_events: int = 500) -> dict:
    """Pull schedule for a league, ingest completed games. Returns counters."""
    league_id = league["id"]
    league_slug = league.get("slug") or ""
    league_name = league.get("name") or ""

    # Persist tournaments for this league
    tournaments = await client.get_tournaments_for_league(league_id)
    for t in tournaments:
        existing = db.get(Tournament, t["id"])
        if not existing:
            existing = Tournament(id=t["id"])
            db.add(existing)
        existing.league_id = league_id
        existing.league_slug = league_slug
        existing.league_name = league_name
        existing.name = t.get("slug") or ""
        existing.start_date = _parse_iso(t.get("startDate"))
        existing.end_date = _parse_iso(t.get("endDate"))
        existing.last_synced = datetime.now(timezone.utc)
    db.commit()

    new_count = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}

    schedule = await client.get_schedule(league_id)
    events = schedule.get("events", []) or []
    page_token = (schedule.get("pages") or {}).get("older")
    pages_fetched = 1
    while page_token and pages_fetched < 6 and len(events) < max_events:
        nxt = await client.get_schedule(league_id, page_token=page_token)
        events.extend(nxt.get("events", []) or [])
        page_token = (nxt.get("pages") or {}).get("older")
        pages_fetched += 1

    for ev in events[:max_events]:
        if ev.get("state") != "completed":
            continue
        match_meta = ev.get("match") or {}
        match_id = match_meta.get("id")
        if not match_id:
            continue

        # Schedule event has only match.id; fetch event details to get games[].
        try:
            details_event = await client.get_event_details(match_id)
        except Exception as exc:
            logger.warning("eventDetails %s failed: %s", match_id, exc)
            continue
        if not details_event:
            continue
        games = ((details_event.get("match") or {}).get("games") or [])

        # Resolve team IDs from event details (schedule gives only code/name)
        teams_detail = (details_event.get("match") or {}).get("teams") or []
        merged_event = {**ev, "match": {**(ev.get("match") or {}), "teams": teams_detail or (ev.get("match") or {}).get("teams", [])}}

        for game in games:
            if game.get("state") != "completed":
                continue
            gid = game.get("id")
            if not gid:
                continue
            event_date = _parse_iso(ev.get("startTime"))
            tournament_id = None
            if event_date:
                for t in tournaments:
                    sd, ed = _parse_iso(t.get("startDate")), _parse_iso(t.get("endDate"))
                    if sd and ed and sd <= event_date <= ed + timedelta(days=2):
                        tournament_id = t["id"]
                        break
            try:
                added, reason = await _ingest_one_game(client, db, gid, merged_event, tournament_id)
                if added:
                    new_count += 1
                else:
                    skipped += 1
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            except Exception as exc:
                logger.warning("game %s ingest failed: %s", gid, exc)
                skipped += 1
                skip_reasons[f"exception:{type(exc).__name__}"] = skip_reasons.get(f"exception:{type(exc).__name__}", 0) + 1

    # Persist teams seen this run.
    # getSchedule teams don't carry IDs; we instead grab them from OfficialMatch
    # rows we just inserted (their blue/red team_id come from getEventDetails).
    seen_team_ids = set()
    for m in db.query(OfficialMatch).filter(OfficialMatch.tournament_id.in_(
        [t["id"] for t in tournaments]
    )).all():
        if m.blue_team_id:
            seen_team_ids.add(m.blue_team_id)
        if m.red_team_id:
            seen_team_ids.add(m.red_team_id)

    # Reconstruct team display info from any participant match we processed
    code_by_id: dict[str, str] = {}
    for ev in events:
        teams = ((ev.get("match") or {}).get("teams") or [])
        # We don't have IDs from schedule, so we rely on the match.code field
        # to seed name/code; only set if we have a matching id later.
    for ev in events:
        for t in ((ev.get("match") or {}).get("teams") or []):
            code = t.get("code")
            if code:
                code_by_id[code] = t  # keyed by code as proxy

    for tid in seen_team_ids:
        pt = db.get(ProTeam, tid)
        if not pt:
            pt = ProTeam(id=tid)
            db.add(pt)
        pt.league_slug = league_slug
    db.commit()

    return {"league": league_slug, "new": new_count, "skipped": skipped,
            "events_seen": len(events), "skip_reasons": skip_reasons}


async def refresh_lec_roster(db: Session) -> int:
    """Compute current LEC roster from the most recently played LEC games."""
    # Find LEC tournament that has the most recently played match.
    lec_tournament_ids = [
        t.id for t in db.query(Tournament).filter(Tournament.league_slug == "lec").all()
    ]
    if not lec_tournament_ids:
        return 0
    latest_match = (
        db.query(OfficialMatch)
        .filter(OfficialMatch.tournament_id.in_(lec_tournament_ids))
        .order_by(OfficialMatch.game_date.desc())
        .first()
    )
    if not latest_match:
        return 0
    active_tour_id = latest_match.tournament_id
    matches = db.query(OfficialMatch).filter_by(tournament_id=active_tour_id).all()
    match_ids = [m.id for m in matches]
    if not match_ids:
        return 0
    parts = (
        db.query(OfficialMatchParticipant)
        .filter(OfficialMatchParticipant.match_id.in_(match_ids))
        .all()
    )
    # Most recent (team, role, player) — keep last seen
    by_key: dict[tuple, OfficialMatchParticipant] = {}
    matches_by_id = {m.id: m for m in matches}
    for mp in parts:
        m = matches_by_id.get(mp.match_id)
        if not m:
            continue
        key = (mp.team_id, mp.role)
        prev = by_key.get(key)
        if not prev or (m.game_date and matches_by_id[prev.match_id].game_date
                        and m.game_date > matches_by_id[prev.match_id].game_date):
            by_key[key] = mp

    db.query(CurrentLECRoster).delete()
    n = 0
    for (team_id, role), mp in by_key.items():
        if not role:
            continue
        team = db.get(ProTeam, team_id)
        latest_match_date = matches_by_id[mp.match_id].game_date
        db.add(CurrentLECRoster(
            team_id=team_id, team_code=team.code if team else "",
            role=role, pro_player_id=mp.pro_player_id,
            player_name=mp.player_name, last_seen=latest_match_date,
        ))
        n += 1
    db.commit()
    return n


async def run_tournament_sync(league_slugs: Iterable[str] = DEFAULT_LEAGUE_SLUGS,
                              max_events_per_league: int = 200) -> dict:
    db = SessionLocal()
    try:
        async with LolesportsClient() as client:
            all_leagues = await client.get_leagues()
            wanted_slugs = set(league_slugs)
            chosen = [l for l in all_leagues if (l.get("slug") or "").lower() in wanted_slugs]
            results = []
            for league in chosen:
                logger.info("ingesting league: %s", league.get("slug"))
                try:
                    res = await ingest_league(client, db, league, max_events=max_events_per_league)
                    results.append(res)
                except Exception as exc:
                    logger.exception("league %s failed", league.get("slug"))
                    results.append({"league": league.get("slug"), "error": str(exc)})

            roster_count = await refresh_lec_roster(db)
            return {
                "leagues_checked": [l.get("slug") for l in chosen],
                "leagues_in_db": len(all_leagues),
                "results": results,
                "lec_roster_size": roster_count,
            }
    finally:
        db.close()


def run_tournament_sync_sync(*args, **kwargs) -> dict:
    return asyncio.run(run_tournament_sync(*args, **kwargs))
