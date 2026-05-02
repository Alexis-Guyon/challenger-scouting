from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, exists
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..db import get_db
from ..models import (
    ChampionPool,
    CSSSnapshot,
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
            "player_image_url": meta.player_image_url,
            "birthdate": meta.birthdate,
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


@router.get("/{puuid}/history")
def player_history(
    puuid: str,
    role: str | None = Query(default=None, description="Filter to a single role"),
    db: Session = Depends(get_db),
):
    """
    Return CSS snapshot history for a player, grouped by role.

    Each snapshot is a (patch, role) state at a given time. The frontend uses
    this to draw "evolution of CSS over X patches" line charts. We dedupe to
    one row per (role, patch) — the latest snapshot wins — so a chart point
    represents the patch's final CSS, not an intermediate ingest snapshot.
    """
    p = db.get(Player, puuid)
    if not p:
        raise HTTPException(404, "player not found")

    q = db.query(CSSSnapshot).filter_by(puuid=puuid)
    if role:
        q = q.filter(CSSSnapshot.role == role.upper())
    snaps = q.order_by(CSSSnapshot.snapshot_at.asc()).all()

    if not snaps:
        return {
            "puuid": puuid,
            "summoner_name": p.summoner_name,
            "by_role": {},
            "patches_count": 0,
            "note": "No snapshots yet. Snapshots are saved at the end of each ingestion. "
                    "Need at least 2 snapshots on different patches to draw a meaningful curve.",
        }

    # Group by role then dedupe to latest snapshot per (role, patch)
    by_role: dict[str, dict[str, CSSSnapshot]] = {}
    for s in snaps:
        if not s.role or not s.patch:
            continue
        bucket = by_role.setdefault(s.role, {})
        prev = bucket.get(s.patch)
        if not prev or s.snapshot_at > prev.snapshot_at:
            bucket[s.patch] = s

    out: dict[str, list[dict]] = {}
    all_patches: set[str] = set()
    for r, patch_to_snap in by_role.items():
        # Sort by patch (lexicographic works well for "16.9" / "16.10" if
        # we pad — but here we sort by snapshot_at as a proxy for chronology)
        sorted_snaps = sorted(patch_to_snap.values(), key=lambda x: x.snapshot_at)
        out[r] = [
            {
                "patch": s.patch,
                "css": round(s.css_score or 0, 1),
                "percentile": round(s.percentile_rank or 0, 1),
                "games": s.games_played,
                "snapshot_at": s.snapshot_at.isoformat() if s.snapshot_at else None,
            }
            for s in sorted_snaps
        ]
        all_patches.update(s.patch for s in sorted_snaps)

    return {
        "puuid": puuid,
        "summoner_name": p.summoner_name,
        "by_role": out,
        "patches_count": len(all_patches),
    }


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
            "pepite_score": round(a.pepite_score, 1) if a.pepite_score is not None else None,
            "pepite_breakdown": (
                __import__("json").loads(a.pepite_breakdown_json)
                if a.pepite_breakdown_json else None
            ),
            "is_rising_star": bool(a.is_rising_star),
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


@router.get("/{puuid}/matchups")
def player_matchups(
    puuid: str,
    role: str | None = Query(default=None, description="Filter to one role (TOP/JGL/MID/ADC/SUP)"),
    min_games: int = Query(default=2, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """For each opponent champion the player has faced (same role, opposite
    team), aggregate winrate / GD@15 / KDA / dmg_share. Powers the
    "vs champion" matchup table on the player profile.

    The query is two-step rather than a self-join: we collect the player's
    own MatchParticipant rows, then for each match_id we find the opposite-
    team same-role participant.
    """
    p = db.get(Player, puuid)
    if not p:
        raise HTTPException(status_code=404, detail="player not found")

    mine_q = db.query(MatchParticipant).filter_by(puuid=puuid)
    if role:
        mine_q = mine_q.filter(MatchParticipant.role == role.upper())
    mine_rows = mine_q.all()
    if not mine_rows:
        return {"matchups": [], "total_games": 0}

    # Pull every opposing-team same-role participant in a single SQL.
    match_ids = [r.match_id for r in mine_rows]
    mine_by_match = {r.match_id: r for r in mine_rows}
    opp_rows = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id.in_(match_ids))
        .filter(MatchParticipant.puuid != puuid)
        .all()
    )
    # Bucket by champion: only count the opp that shares the player's role
    from collections import defaultdict
    buckets: dict[str, list] = defaultdict(list)
    for opp in opp_rows:
        mine = mine_by_match.get(opp.match_id)
        if not mine or opp.role != mine.role or opp.team_id == mine.team_id:
            continue
        buckets[opp.champion_name or "Unknown"].append((mine, opp))

    matchups = []
    for champ, pairs in buckets.items():
        if len(pairs) < min_games:
            continue
        n = len(pairs)
        wins = sum(1 for m, _ in pairs if m.win)
        gd15 = sum(m.gd_at_15 for m, _ in pairs) / n
        csd15 = sum(m.csd_at_15 for m, _ in pairs) / n
        kda = sum(m.kda for m, _ in pairs) / n
        dmg_share = sum(m.damage_share for m, _ in pairs) / n
        matchups.append({
            "champion": champ,
            "games": n,
            "wins": wins,
            "winrate": round(wins / n * 100, 1),
            "avg_gd15": round(gd15, 1),
            "avg_csd15": round(csd15, 1),
            "avg_kda": round(kda, 2),
            "avg_dmg_share": round(dmg_share, 3),
        })
    matchups.sort(key=lambda x: -x["games"])
    return {"matchups": matchups, "total_games": len(mine_rows)}


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
    rising_only: bool = Query(default=False, description="Only players tagged is_rising_star"),
    include_unresolved: bool = Query(default=False, description="Include stub players whose Riot name failed to resolve (shown as '(unknown)')"),
    tier: str | None = Query(default=None, description="Filter by latest rank tier: CHALLENGER, GRANDMASTER, MASTER"),
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

    # Require an actual ranked SoloQ tier on file. Without this, players we
    # ingested but never got a rank snapshot for (account de-ranked / new
    # account / API hiccup) sneak onto the Challenger ladder with empty
    # Tier / LP columns.
    q = q.filter(
        exists().where(
            (RankSnapshot.puuid == Player.puuid)
            & RankSnapshot.tier.isnot(None)
        )
    )

    # Optional filter on the LATEST rank tier per player (CHALLENGER / GM /
    # MASTER). We resolve "latest" via a (puuid -> max(snapshot_date))
    # subquery so a player who used to be Challenger but is now Master
    # gets categorised by their current tier.
    if tier:
        from sqlalchemy import func as _func
        from sqlalchemy.orm import aliased
        tier_norm = tier.strip().upper()
        if tier_norm not in ("CHALLENGER", "GRANDMASTER", "MASTER"):
            from fastapi import HTTPException
            raise HTTPException(400, f"unknown tier {tier!r}; expected CHALLENGER, GRANDMASTER, MASTER")
        _latest_dates_for_tier = (
            db.query(
                RankSnapshot.puuid.label("puuid"),
                _func.max(RankSnapshot.snapshot_date).label("max_date"),
            )
            .filter(RankSnapshot.tier.isnot(None))
            .group_by(RankSnapshot.puuid)
            .subquery()
        )
        _LatestForTier = aliased(RankSnapshot)
        q = (
            q.join(_latest_dates_for_tier, _latest_dates_for_tier.c.puuid == Player.puuid)
             .join(
                 _LatestForTier,
                 (_LatestForTier.puuid == _latest_dates_for_tier.c.puuid)
                 & (_LatestForTier.snapshot_date == _latest_dates_for_tier.c.max_date),
             )
             .filter(_LatestForTier.tier == tier_norm)
        )

    if pro_only:
        q = q.filter(PlayerMeta.is_pro == True)  # noqa: E712
    if rising_only:
        q = q.filter(PlayerAggregate.is_rising_star == True)  # noqa: E712
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
    elif sort == "pepite":
        # Composite scout score — see services/scoring.compute_pepite_score
        q = q.order_by(desc(PlayerAggregate.pepite_score))
    elif sort == "winrate":
        q = q.order_by(desc(PlayerAggregate.wins * 1.0 / PlayerAggregate.games_played))
    elif sort == "games":
        q = q.order_by(desc(PlayerAggregate.games_played))
    elif sort == "lp":
        # Sort by the LP of the *latest* RankSnapshot per player. A naive
        # outerjoin on RankSnapshot multiplies rows (228 players have >1
        # snapshot in DB) and ends up sorting on the historical max LP
        # rather than current LP. Build a (puuid -> latest_date) subquery
        # then join the actual snapshot row at that date for its LP.
        from sqlalchemy import func as _func
        from sqlalchemy.orm import aliased
        latest_dates = (
            db.query(
                RankSnapshot.puuid.label("puuid"),
                _func.max(RankSnapshot.snapshot_date).label("max_date"),
            )
            .filter(RankSnapshot.tier.isnot(None))
            .group_by(RankSnapshot.puuid)
            .subquery()
        )
        LatestRank = aliased(RankSnapshot)
        q = (
            q.join(latest_dates, latest_dates.c.puuid == Player.puuid)
             .join(
                 LatestRank,
                 (LatestRank.puuid == latest_dates.c.puuid)
                 & (LatestRank.snapshot_date == latest_dates.c.max_date),
             )
             .order_by(desc(LatestRank.lp))
        )
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
            "is_rising_star": bool(a.is_rising_star),
            "pepite_score": round(a.pepite_score, 1) if a.pepite_score is not None else None,
        })
        if len(out) >= limit:
            break
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": out,
    }
