"""
Seed synthetic Challenger-like data so the UI can be demoed without a Riot API key.

Usage (from the `backend/` directory):
    python scripts/seed_demo.py [num_players_per_role]
"""
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine
from app.models import (
    ChampionPool, Match, MatchParticipant, Player, PlayerAggregate, RankSnapshot,
)
from app.services.aggregation import aggregate_all_players, compute_role_distributions
from app.services.scoring import score_all

random.seed(42)
Base.metadata.create_all(bind=engine)

ROLES = ["TOP", "JGL", "MID", "ADC", "SUP"]
PATCH = "14.9"

CHAMPIONS = {
    "TOP": [(86, "Garen"), (122, "Darius"), (266, "Aatrox"), (54, "Malphite"), (114, "Fiora"), (75, "Nasus"), (24, "Jax")],
    "JGL": [(64, "LeeSin"), (60, "Elise"), (102, "Shyvana"), (76, "Nidalee"), (203, "Kindred"), (104, "Graves"), (113, "Sejuani")],
    "MID": [(157, "Yasuo"), (103, "Ahri"), (7, "LeBlanc"), (61, "Orianna"), (134, "Syndra"), (105, "Fizz"), (45, "Veigar")],
    "ADC": [(222, "Jinx"), (51, "Caitlyn"), (236, "Lucian"), (22, "Ashe"), (110, "Varus"), (145, "Kaisa"), (498, "Xayah")],
    "SUP": [(412, "Thresh"), (89, "Leona"), (267, "Nami"), (40, "Janna"), (350, "Yuumi"), (53, "Blitzcrank"), (12, "Alistar")],
}

# Mean baselines per role (what a typical Challenger looks like)
ROLE_BASE = {
    "TOP": dict(gd15=20, xpd15=30, csd15=2, cspm=8.5, dmg_share=0.22, dpm=580, kp=0.55, kda=2.6,
                vspm=0.95, wpm=0.45, wcpm=0.25, solo_kills=0.6, obj_dmg=2500, early_deaths=0.7, deaths=4.2),
    "JGL": dict(gd15=10, xpd15=20, csd15=0, cspm=5.8, dmg_share=0.18, dpm=480, kp=0.65, kda=3.2,
                vspm=1.10, wpm=0.55, wcpm=0.40, solo_kills=0.4, obj_dmg=8000, early_deaths=0.6, deaths=4.0),
    "MID": dict(gd15=50, xpd15=60, csd15=4, cspm=8.7, dmg_share=0.27, dpm=720, kp=0.60, kda=3.0,
                vspm=1.10, wpm=0.50, wcpm=0.35, solo_kills=0.5, obj_dmg=2200, early_deaths=0.6, deaths=4.1),
    "ADC": dict(gd15=15, xpd15=10, csd15=2, cspm=9.5, dmg_share=0.30, dpm=820, kp=0.55, kda=2.9,
                vspm=1.05, wpm=0.40, wcpm=0.20, solo_kills=0.3, obj_dmg=4500, early_deaths=0.5, deaths=4.5),
    "SUP": dict(gd15=-30, xpd15=-20, csd15=-1, cspm=1.2, dmg_share=0.10, dpm=240, kp=0.68, kda=3.5,
                vspm=2.40, wpm=1.20, wcpm=0.85, solo_kills=0.1, obj_dmg=900, early_deaths=0.5, deaths=4.7),
}


def create_player(db, idx, role):
    puuid = f"demo-{role}-{idx}"
    name = f"{role}_Player_{idx:02d}"
    p = Player(
        puuid=puuid, summoner_id=f"sid-{puuid}", summoner_name=name,
        region="euw1", main_role=role,
        account_level=random.randint(80, 600),
        smurf_flag=False,
        last_updated=datetime.now(timezone.utc),
    )
    db.add(p)
    db.add(RankSnapshot(
        puuid=puuid, tier="CHALLENGER", rank="I",
        lp=random.randint(450, 1500),
        wins=random.randint(180, 400), losses=random.randint(170, 380),
        snapshot_date=datetime.now(timezone.utc),
    ))
    return p


def create_match(db, players_in_match, idx):
    """Create one synthetic match with 10 players (we use only the role-mapped ones we got)."""
    mid = f"DEMO_{idx:06d}"
    duration_min = random.randint(22, 38)
    blue_win = random.random() < 0.5
    m = Match(
        match_id=mid, region="europe", patch=PATCH,
        game_creation=datetime.now(timezone.utc) - timedelta(days=random.randint(0, 14)),
        game_duration_sec=duration_min * 60,
        queue_id=420, blue_win=blue_win,
    )
    db.add(m)

    team_kills = {100: random.randint(15, 35), 200: random.randint(15, 35)}
    team_dmg = {100: 0, 200: 0}
    parts = []
    for i, (player, team_id, role) in enumerate(players_in_match):
        base = ROLE_BASE[role]
        # Per-player skill multiplier (consistent across matches)
        skill = getattr(player, "_skill", 1.0)
        # Add per-match noise
        noise = random.gauss(1.0, 0.18)
        cs_total = max(0, int((base["cspm"] * duration_min) * skill * noise))
        dmg = int((base["dpm"] * duration_min) * skill * random.gauss(1.0, 0.20))
        team_dmg[team_id] += dmg
        win = (team_id == 100 and blue_win) or (team_id == 200 and not blue_win)
        kills = max(0, int(random.gauss(8 * skill, 3)))
        deaths = max(1, int(random.gauss(base["deaths"] / max(skill, 0.5), 1.5)))
        assists = max(0, int(random.gauss(team_kills[team_id] * 0.55, 3)))
        cid, cname = random.choice(CHAMPIONS[role])
        parts.append(dict(
            player=player, team_id=team_id, role=role, win=win,
            cs=cs_total, dmg=dmg, kills=kills, deaths=deaths, assists=assists,
            cid=cid, cname=cname, base=base, skill=skill, noise=noise, duration_min=duration_min,
        ))

    for pp in parts:
        base = pp["base"]; skill = pp["skill"]
        gd15 = int(random.gauss(base["gd15"] * skill, 280))
        xpd15 = int(random.gauss(base["xpd15"] * skill, 260))
        csd15 = int(random.gauss(base["csd15"] * skill, 6))
        cs15 = int(base["cspm"] * 15 * skill * random.gauss(1.0, 0.15))
        cs10 = int(base["cspm"] * 10 * skill * random.gauss(1.0, 0.15))
        vision = max(0, int(random.gauss(base["vspm"] * pp["duration_min"] * skill, 8)))
        wp = max(0, int(random.gauss(base["wpm"] * pp["duration_min"] * skill, 4)))
        wk = max(0, int(random.gauss(base["wcpm"] * pp["duration_min"] * skill, 2)))
        ctrl = max(0, int(random.gauss(2 * skill, 1)))
        sk = max(0, int(random.gauss(base["solo_kills"] * skill * pp["duration_min"]/30, 1)))
        obj = max(0, int(random.gauss(base["obj_dmg"] * skill, 1500)))
        ed = max(0, int(random.gauss(base["early_deaths"] / max(skill, 0.5), 0.6)))

        tdmg = team_dmg[pp["team_id"]] or 1
        tkills = team_kills[pp["team_id"]]
        dmg_share = pp["dmg"] / tdmg
        kp = (pp["kills"] + pp["assists"]) / tkills if tkills else 0
        kda = (pp["kills"] + pp["assists"]) / max(pp["deaths"], 1)

        mp = MatchParticipant(
            match_id=mid, puuid=pp["player"].puuid, team_id=pp["team_id"], role=pp["role"],
            champion_id=pp["cid"], champion_name=pp["cname"], win=pp["win"],
            kills=pp["kills"], deaths=pp["deaths"], assists=pp["assists"],
            cs_total=pp["cs"], gold_earned=int(pp["cs"] * 50 + pp["kills"] * 300),
            damage_to_champs=pp["dmg"], damage_taken=int(pp["dmg"] * 0.8),
            vision_score=vision, wards_placed=wp, wards_killed=wk, control_wards=ctrl,
            solo_kills=sk, objective_dmg=obj,
            dragon_kills=random.randint(0, 2) if pp["role"] in ("JGL","ADC","SUP") else 0,
            baron_kills=random.randint(0, 1) if pp["role"] in ("JGL","TOP") else 0,
            turret_kills=random.randint(0, 3),
            gd_at_15=gd15, xpd_at_15=xpd15, csd_at_15=csd15,
            cs_at_10=cs10, cs_at_15=cs15, early_deaths=ed,
            damage_share=dmg_share, kill_participation=kp, kda=kda,
        )
        db.add(mp)


def seed(num_per_role: int = 8, matches_per_player: int = 25):
    db = SessionLocal()
    try:
        # Clean slate
        for model in [ChampionPool, PlayerAggregate, MatchParticipant, Match, RankSnapshot, Player]:
            db.query(model).delete()
        db.commit()

        # Create players with skill levels — some elite, some median
        all_players = {role: [] for role in ROLES}
        for role in ROLES:
            for i in range(num_per_role):
                p = create_player(db, i, role)
                # skill: 0.85..1.25 with a couple of standout candidates
                if i == 0:
                    p._skill = 1.30  # elite prospect
                elif i == 1:
                    p._skill = 1.18
                else:
                    p._skill = random.uniform(0.85, 1.10)
                all_players[role].append(p)
        db.commit()

        # Create matches: each match draws 2 players per role from pool
        match_idx = 0
        # We need each player to have ~matches_per_player games. Create batches.
        target_total_participations = num_per_role * matches_per_player  # per role
        matches_total = target_total_participations // 2  # each match has 2 players per role
        for _ in range(matches_total):
            participants = []
            for role in ROLES:
                p1, p2 = random.sample(all_players[role], 2)
                participants.append((p1, 100, role))
                participants.append((p2, 200, role))
            create_match(db, participants, match_idx)
            match_idx += 1
            if match_idx % 50 == 0:
                db.commit()
                print(f"  ...{match_idx} matches")
        db.commit()
        print(f"Created {match_idx} matches.")

        print("Aggregating...")
        aggregate_all_players(db)
        print("Computing distributions...")
        compute_role_distributions(db, min_games=1)
        print("Scoring...")
        n = score_all(db, min_games=1)
        print(f"Scored {n} aggregates.")
    finally:
        db.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    seed(num_per_role=n)
    print("Done. Run: uvicorn app.main:app --reload")
