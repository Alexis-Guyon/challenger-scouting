"""
Parse Riot match timeline (frame-by-frame) to extract advanced metrics:
- Gold/XP/CS diff @15 vs lane opponent
- Solo kills (kill where assistingParticipantIds is empty AND only 1 other on map nearby)
- Early deaths (before 14:00)
"""
from typing import Any


ROLE_MAP = {
    "TOP": "TOP",
    "JUNGLE": "JGL",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUP",
}


def normalize_role(team_position: str) -> str:
    return ROLE_MAP.get(team_position, team_position)


def find_lane_opponent(participants: list[dict], me_pid: int) -> int | None:
    """Match by teamPosition across teams."""
    me = next((p for p in participants if p["participantId"] == me_pid), None)
    if not me:
        return None
    role = me.get("teamPosition")
    if not role:
        return None
    for p in participants:
        if p["participantId"] != me_pid and p.get("teamPosition") == role:
            return p["participantId"]
    return None


def extract_at_minute(frames: list[dict], minute: int) -> dict | None:
    """Return the participantFrames dict at the given minute (or last frame before it)."""
    target_ms = minute * 60 * 1000
    chosen = None
    for f in frames:
        if f.get("timestamp", 0) <= target_ms + 30_000:
            chosen = f
        else:
            break
    return chosen


def diff_at_minute(timeline: dict, me_pid: int, opp_pid: int, minute: int) -> dict:
    """Return {gd, xpd, csd, cs_me} at given minute."""
    info = timeline.get("info", {})
    frames = info.get("frames", [])
    frame = extract_at_minute(frames, minute)
    if not frame:
        return {"gd": 0, "xpd": 0, "csd": 0, "cs_me": 0}
    pf = frame.get("participantFrames", {})
    me = pf.get(str(me_pid), {})
    opp = pf.get(str(opp_pid), {}) if opp_pid else {}
    cs_me = me.get("minionsKilled", 0) + me.get("jungleMinionsKilled", 0)
    cs_opp = opp.get("minionsKilled", 0) + opp.get("jungleMinionsKilled", 0)
    return {
        "gd": me.get("totalGold", 0) - opp.get("totalGold", 0),
        "xpd": me.get("xp", 0) - opp.get("xp", 0),
        "csd": cs_me - cs_opp,
        "cs_me": cs_me,
    }


def count_solo_kills_and_early_deaths(timeline: dict, me_pid: int) -> tuple[int, int]:
    info = timeline.get("info", {})
    frames = info.get("frames", [])
    solo_kills = 0
    early_deaths = 0
    for frame in frames:
        for ev in frame.get("events", []):
            if ev.get("type") == "CHAMPION_KILL":
                killer = ev.get("killerId")
                victim = ev.get("victimId")
                assistants = ev.get("assistingParticipantIds") or []
                ts = ev.get("timestamp", 0)
                if killer == me_pid and not assistants:
                    solo_kills += 1
                if victim == me_pid and ts <= 14 * 60 * 1000:
                    early_deaths += 1
    return solo_kills, early_deaths


def parse_match_advanced(match: dict, timeline: dict) -> list[dict]:
    """For each participant, compute advanced metrics. Returns list of dicts keyed by puuid."""
    info = match.get("info", {})
    participants = info.get("participants", [])
    out = []

    for p in participants:
        pid = p["participantId"]
        opp_pid = find_lane_opponent(participants, pid)

        d15 = diff_at_minute(timeline, pid, opp_pid, 15) if opp_pid else {"gd": 0, "xpd": 0, "csd": 0, "cs_me": 0}
        d10 = diff_at_minute(timeline, pid, opp_pid, 10) if opp_pid else {"gd": 0, "xpd": 0, "csd": 0, "cs_me": 0}

        solo_kills, early_deaths = count_solo_kills_and_early_deaths(timeline, pid)

        out.append({
            "puuid": p["puuid"],
            "participant_id": pid,
            "role": normalize_role(p.get("teamPosition", "")),
            "champion_id": p.get("championId"),
            "champion_name": p.get("championName"),
            "team_id": p.get("teamId"),
            "win": p.get("win"),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
            "cs_total": p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
            "gold_earned": p.get("goldEarned", 0),
            "damage_to_champs": p.get("totalDamageDealtToChampions", 0),
            "damage_taken": p.get("totalDamageTaken", 0),
            "vision_score": p.get("visionScore", 0),
            "wards_placed": p.get("wardsPlaced", 0),
            "wards_killed": p.get("wardsKilled", 0),
            "control_wards": p.get("visionWardsBoughtInGame", 0),
            "objective_dmg": p.get("damageDealtToObjectives", 0),
            "dragon_kills": p.get("dragonKills", 0),
            "baron_kills": p.get("baronKills", 0),
            "turret_kills": p.get("turretKills", 0),
            "gd_at_15": d15["gd"],
            "xpd_at_15": d15["xpd"],
            "csd_at_15": d15["csd"],
            "cs_at_10": d10["cs_me"],
            "cs_at_15": d15["cs_me"],
            "solo_kills": solo_kills,
            "early_deaths": early_deaths,
        })
    return out
