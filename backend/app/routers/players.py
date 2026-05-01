from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..db import get_db
from ..models import (
    ChampionPool,
    MatchParticipant,
    Player,
    PlayerAggregate,
    PlayerMeta,
    RankSnapshot,
    User,
    WatchlistEntry,
)
from ..services.scoring import compute_css_for_aggregate

router = APIRouter(prefix="/players", tags=["players"], dependencies=[Depends(get_current_user)])


def _serialize_player(p: Player, db: Session) -> dict:
    latest_rank = (
        db.query(RankSnapshot)
        .filter_by(puuid=p.puuid)
        .order_by(desc(RankSnapshot.snapshot_date))
        .first()
    )
    meta = db.get(PlayerMeta, p.puuid)
    meta_payload = None
    if meta and meta.is_pro:
        import json as _json
        profile = None
        if meta.lolpros_profile_json:
            try:
                profile = _json.loads(meta.lolpros_profile_json)
            except Exception:
                profile = None

        def _https(url: str | None) -> str | None:
            if not url:
                return url
            if url.startswith("http://"):
                return "https://" + url[len("http://"):]
            return url
        meta_payload = {
            "leaguepedia_id": meta.leaguepedia_id,
            "leaguepedia_url": meta.leaguepedia_url,
            "lolpros_slug": meta.lolpros_slug,
            "lolpros_url": f"https://lolpros.gg/player/{meta.lolpros_slug}" if meta.lolpros_slug else None,
            "country": meta.country,
            "nationality_primary": meta.nationality_primary,
            "residency": meta.residency,
            "age": meta.age,
            "lp_role": meta.role,
            "current_team": meta.current_team,
            "current_team_tag": meta.current_team_tag,
            "current_team_logo_url": _https(meta.current_team_logo_url),
            "is_fa": (meta.current_team or "") == "" and not meta.is_retired,
            "is_retired": meta.is_retired,
            "contract_end": meta.contract_end,
            # Profile sub-payload — only the bits the UI actually needs
            "social_media": (profile or {}).get("social_media"),
            "previous_teams": [
                {
                    "name": pt.get("name"),
                    "tag": pt.get("tag"),
                    "slug": pt.get("slug"),
                    "logo_url": _https((pt.get("logo") or {}).get("url")),
                    "join_date": pt.get("join_date"),
                    "leave_date": pt.get("leave_date"),
                }
                for pt in (profile or {}).get("previous_teams", []) or []
            ],
            "other_countries": (profile or {}).get("other_countries", []),
            "score": ((profile or {}).get("league_player") or {}).get("score"),
            "in_game": ((profile or {}).get("league_player") or {}).get("in_game"),
            "accounts": [
                {
                    "server": acc.get("server"),
                    "summoner_name": acc.get("summoner_name"),
                    "gamename": acc.get("gamename"),
                    "tagline": acc.get("tagline"),
                    "profile_icon_id": acc.get("profile_icon_id"),
                    "summoner_names_history": [
                        sn.get("name") for sn in (acc.get("summoner_names") or [])
                        if sn.get("name") and sn.get("name") != acc.get("summoner_name")
                    ][:6],
                    "rank": acc.get("rank"),
                    "peak": acc.get("peak"),
                }
                for acc in ((profile or {}).get("league_player") or {}).get("accounts", []) or []
            ],
            "leagues": [
                {
                    "name": lg.get("name"),
                    "shorthand": lg.get("shorthand"),
                    "slug": lg.get("slug"),
                    "logo_url": _https((lg.get("logo") or {}).get("url")),
                }
                for lg in (profile or {}).get("leagues", []) or []
            ],
        }
    import json as _json
    smurf_signals = None
    if p.smurf_signals:
        try:
            smurf_signals = _json.loads(p.smurf_signals)
        except Exception:
            smurf_signals = None
    return {
        "puuid": p.puuid,
        "summoner_name": p.summoner_name,
        "region": p.region,
        "main_role": p.main_role,
        "account_level": p.account_level,
        "smurf_flag": p.smurf_flag,
        "smurf_score": round(p.smurf_score or 0.0, 2),
        "smurf_signals": smurf_signals,
        "tier": latest_rank.tier if latest_rank else None,
        "lp": latest_rank.lp if latest_rank else None,
        "wins": latest_rank.wins if latest_rank else None,
        "losses": latest_rank.losses if latest_rank else None,
        "meta": meta_payload,
    }


@router.get("/search")
def search_players(name: str = Query(...), db: Session = Depends(get_db)):
    rows = (
        db.query(Player)
        .filter(Player.summoner_name.ilike(f"%{name}%"))
        .limit(20)
        .all()
    )
    return [_serialize_player(p, db) for p in rows]


@router.get("/{puuid}")
def get_player(
    puuid: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = db.get(Player, puuid)
    if not p:
        raise HTTPException(status_code=404, detail="player not found")

    aggs = db.query(PlayerAggregate).filter_by(puuid=puuid).all()
    agg_payload = []
    for a in aggs:
        _, _, breakdown = compute_css_for_aggregate(db, a)
        agg_payload.append({
            "patch": a.patch,
            "role": a.role,
            "games_played": a.games_played,
            "wins": a.wins,
            "winrate": round((a.wins / a.games_played * 100) if a.games_played else 0, 1),
            "css_score": round(a.css_score, 1),
            "percentile_rank": a.percentile_rank,
            "stats": {
                "gd15": round(a.avg_gd15, 1),
                "xpd15": round(a.avg_xpd15, 1),
                "csd15": round(a.avg_csd15, 1),
                "cspm": round(a.avg_cspm, 2),
                "dmg_share": round(a.avg_dmg_share, 3),
                "dpm": round(a.avg_dpm, 1),
                "kp": round(a.avg_kp, 3),
                "kda": round(a.avg_kda, 2),
                "vspm": round(a.avg_vspm, 2),
                "wpm": round(a.avg_wpm, 2),
                "wcpm": round(a.avg_wcpm, 2),
                "solo_kills": round(a.avg_solo_kills, 2),
                "objective_dmg": round(a.avg_objective_dmg, 1),
                "early_deaths": round(a.avg_early_deaths, 2),
                "deaths": round(a.avg_deaths, 2),
                "champion_pool_size": a.champion_pool_size,
                "std_gd15": round(a.std_gd15, 1),
            },
            "breakdown": breakdown,
        })

    pool = (
        db.query(ChampionPool)
        .filter_by(puuid=puuid)
        .order_by(desc(ChampionPool.games))
        .limit(20)
        .all()
    )
    pool_payload = [
        {
            "champion_id": cp.champion_id,
            "champion_name": cp.champion_name,
            "patch": cp.patch,
            "role": cp.role,
            "games": cp.games,
            "wins": cp.wins,
            "winrate": round((cp.wins / cp.games * 100) if cp.games else 0, 1),
            "avg_kda": round(cp.avg_kda, 2),
            "avg_dmg_share": round(cp.avg_dmg_share, 3),
            "avg_kp": round(cp.avg_kp, 3),
            "avg_gd15": round(cp.avg_gd15, 1),
            "avg_csd15": round(cp.avg_csd15, 1),
            "avg_dpm": round(cp.avg_dpm, 1),
            # Per-champion CSS vs same-champion Challenger baseline (None if N<10)
            "champion_css": cp.champion_css if cp.has_champion_baseline else None,
            "has_baseline": cp.has_champion_baseline,
        }
        for cp in pool
    ]

    recent = (
        db.query(MatchParticipant)
        .filter_by(puuid=puuid)
        .order_by(desc(MatchParticipant.id))
        .limit(20)
        .all()
    )
    recent_payload = [
        {
            "match_id": r.match_id,
            "champion_name": r.champion_name,
            "role": r.role,
            "win": r.win,
            "kills": r.kills, "deaths": r.deaths, "assists": r.assists,
            "kda": round(r.kda, 2),
            "cs": r.cs_total,
            "gd15": r.gd_at_15, "xpd15": r.xpd_at_15, "csd15": r.csd_at_15,
            "dmg_share": round(r.damage_share, 3),
            "vision_score": r.vision_score,
        }
        for r in recent
    ]

    is_watched = (
        db.query(WatchlistEntry)
        .filter_by(user_id=user.id, puuid=puuid)
        .first() is not None
    )

    return {
        "player": _serialize_player(p, db),
        "aggregates": agg_payload,
        "champion_pool": pool_payload,
        "recent_matches": recent_payload,
        "is_watched": is_watched,
    }


@router.get("")
def list_players(
    role: str | None = None,
    patch: str | None = None,
    min_games: int = Query(default=None),
    sort: str = "css",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    # --- Advanced scouting filters (PlayerMeta) ---
    fa: bool | None = Query(default=None, description="Free agents only (no current team)"),
    contract_within_days: int | None = Query(default=None, description="Contract ending within N days"),
    max_age: int | None = Query(default=None, description="Cap player age (e.g. 21 for U21)"),
    min_age: int | None = Query(default=None),
    residency: str | None = Query(default=None, description='e.g. "Europe", "Korea"'),
    country: str | None = Query(default=None, description='e.g. "France"'),
    pro_only: bool = Query(default=False),
    include_unresolved: bool = Query(default=False, description="Include stub players whose Riot name failed to resolve (shown as '(unknown)')"),
    db: Session = Depends(get_db),
):
    """Scout leaderboard. Default sort = CSS desc."""
    from datetime import date, timedelta

    min_games = min_games if min_games is not None else settings.min_games

    q = (
        db.query(PlayerAggregate, Player)
        .join(Player, PlayerAggregate.puuid == Player.puuid)
        .outerjoin(PlayerMeta, PlayerMeta.puuid == Player.puuid)
    )
    if role:
        q = q.filter(PlayerAggregate.role == role.upper())
    if patch:
        q = q.filter(PlayerAggregate.patch == patch)
    q = q.filter(PlayerAggregate.games_played >= min_games)

    if not include_unresolved:
        # By default, hide stub players (Riot ID never resolved during ingestion).
        # A real Riot ID always contains "#" (gameName#tagLine). Stubs are either
        # "(unknown)", "" or the 8-char puuid-prefix fallback — none have "#".
        q = q.filter(Player.summoner_name.isnot(None))
        q = q.filter(Player.summoner_name != "(unknown)")
        q = q.filter(Player.summoner_name != "")
        q = q.filter(Player.summoner_name.like("%#%"))

    if pro_only:
        q = q.filter(PlayerMeta.is_pro == True)  # noqa: E712
    if fa is True:
        q = q.filter(PlayerMeta.is_pro == True, (PlayerMeta.current_team == "") | (PlayerMeta.current_team.is_(None)), PlayerMeta.is_retired == False)  # noqa: E712
    if contract_within_days is not None:
        cutoff = (date.today() + timedelta(days=contract_within_days)).isoformat()
        q = q.filter(PlayerMeta.contract_end.isnot(None), PlayerMeta.contract_end != "", PlayerMeta.contract_end <= cutoff)
    if max_age is not None:
        q = q.filter(PlayerMeta.age.isnot(None), PlayerMeta.age <= max_age)
    if min_age is not None:
        q = q.filter(PlayerMeta.age.isnot(None), PlayerMeta.age >= min_age)
    if residency:
        q = q.filter(PlayerMeta.residency == residency)
    if country:
        q = q.filter(PlayerMeta.country == country)

    if sort == "css":
        q = q.order_by(desc(PlayerAggregate.css_score))
    elif sort == "winrate":
        q = q.order_by(desc(PlayerAggregate.wins * 1.0 / PlayerAggregate.games_played))
    elif sort == "games":
        q = q.order_by(desc(PlayerAggregate.games_played))
    elif sort == "lp":
        q = q.outerjoin(RankSnapshot, RankSnapshot.puuid == Player.puuid).order_by(desc(RankSnapshot.lp))
    elif sort == "age":
        q = q.order_by(PlayerMeta.age.asc().nullslast())
    else:
        q = q.order_by(desc(PlayerAggregate.css_score))

    # Total count for pagination — same query without limit/offset
    total = q.count()

    rows = q.offset(offset).limit(limit + 50).all()  # +50 buffer for de-dup loss
    seen = set()
    out = []
    for a, p in rows:
        if p.puuid in seen:
            continue
        seen.add(p.puuid)
        out.append({
            **_serialize_player(p, db),
            "patch": a.patch,
            "role": a.role,
            "games_played": a.games_played,
            "wins": a.wins,
            "winrate": round((a.wins / a.games_played * 100) if a.games_played else 0, 1),
            "css_score": round(a.css_score, 1),
            "percentile_rank": a.percentile_rank,
            "champion_pool_size": a.champion_pool_size,
        })
        if len(out) >= limit:
            break
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": out,
    }
