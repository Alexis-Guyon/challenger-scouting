"""
Alerts engine — Discord + Slack webhook notifications.

Run after each ingestion via run_alerts_check(db). Compares the latest
PlayerAggregate state to the previous CSS snapshot and emits one webhook
post per signal:

  RISING_STAR     CSS gained ≥ alert_css_delta points since last snapshot
  ELITE_THRESHOLD CSS crossed alert_css_min for the first time
  WIN_STREAK      ≥ alert_winrate_streak_min consecutive wins on the most
                  recent matches (looking at MatchParticipant order)
  WATCHLIST_DELTA  any watched player whose CSS moved by ≥ delta

The previous-snapshot mechanism uses the CSSSnapshot table — we save a
fresh snapshot at the END of every alerts check.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    CSSSnapshot,
    MatchParticipant,
    Player,
    PlayerAggregate,
    PlayerMeta,
    WatchlistEntry,
)

logger = logging.getLogger(__name__)


# ---------- Webhook senders ----------

def _post_discord(content: str, embeds: list[dict] | None = None) -> bool:
    if not settings.discord_webhook_url:
        return False
    try:
        payload = {"content": content[:2000]}
        if embeds:
            payload["embeds"] = embeds[:10]
        with httpx.Client(timeout=10.0) as client:
            r = client.post(settings.discord_webhook_url, json=payload)
        if r.status_code >= 300:
            logger.warning("Discord webhook %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("Discord post failed: %s", exc)
        return False


def _post_slack(text: str, blocks: list[dict] | None = None) -> bool:
    if not settings.slack_webhook_url:
        return False
    try:
        payload: dict = {"text": text[:3000]}
        if blocks:
            payload["blocks"] = blocks
        with httpx.Client(timeout=10.0) as client:
            r = client.post(settings.slack_webhook_url, json=payload)
        if r.status_code >= 300:
            logger.warning("Slack webhook %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("Slack post failed: %s", exc)
        return False


def _send(text: str) -> int:
    """Send a plain message to every configured webhook. Returns count of successes."""
    n = 0
    if _post_discord(text):
        n += 1
    if _post_slack(text):
        n += 1
    return n


# ---------- Detection helpers ----------

def _player_link(puuid: str) -> str:
    base = settings.public_app_url.rstrip("/")
    if not base:
        return f"puuid={puuid[:12]}…"
    return f"{base}/?puuid={puuid}"


def _format_player(p: Player, meta: PlayerMeta | None) -> str:
    name = p.summoner_name or p.puuid[:8]
    bits = [name]
    if meta and meta.is_pro:
        team = meta.current_team_tag or meta.current_team or "FA"
        bits.append(f"({team})")
    return " ".join(bits)


def _last_n_results(db: Session, puuid: str, n: int = 10) -> list[bool]:
    rows = (
        db.query(MatchParticipant.win)
        .filter_by(puuid=puuid)
        .order_by(desc(MatchParticipant.id))
        .limit(n)
        .all()
    )
    return [r[0] for r in rows]


def _detect_streak(results: list[bool]) -> int:
    """Returns length of current win streak (most recent first)."""
    streak = 0
    for win in results:
        if win:
            streak += 1
        else:
            break
    return streak


# ---------- Main entry ----------

def run_alerts_check(db: Session) -> int:
    """
    Compare current state to the previous snapshot, emit alerts, save a new
    snapshot. Returns the count of alerts sent.

    Snapshots are saved REGARDLESS of webhook configuration — they're needed
    by the rising-star detector even when no webhook is set up.
    """
    has_webhook = bool(settings.discord_webhook_url or settings.slack_webhook_url)
    sent = 0
    now = datetime.now(timezone.utc)

    # Build previous-snapshot index: (puuid, patch, role) -> CSSSnapshot
    prev_snapshots: dict[tuple, CSSSnapshot] = {}
    for snap in db.query(CSSSnapshot).order_by(desc(CSSSnapshot.snapshot_at)).all():
        key = (snap.puuid, snap.patch, snap.role)
        if key not in prev_snapshots:
            prev_snapshots[key] = snap

    # Build watchlist set (every analyst's watchlist, deduped)
    watched_puuids: set[str] = {
        w.puuid for w in db.query(WatchlistEntry).all()
    }

    # Collect alerts
    new_snapshots: list[CSSSnapshot] = []
    rising = []
    elite = []
    watch_deltas = []
    win_streaks = []

    aggs = db.query(PlayerAggregate).filter(PlayerAggregate.css_score > 0).all()
    for agg in aggs:
        key = (agg.puuid, agg.patch, agg.role)
        prev = prev_snapshots.get(key)
        prev_css = prev.css_score if prev else None

        # Save the new snapshot
        new_snapshots.append(CSSSnapshot(
            puuid=agg.puuid, patch=agg.patch, role=agg.role,
            css_score=agg.css_score, percentile_rank=agg.percentile_rank,
            games_played=agg.games_played, snapshot_at=now,
        ))

        # Filter: only above CSS floor for non-watchlist signals
        is_watched = agg.puuid in watched_puuids

        # 1. Rising star
        if prev_css is not None and (agg.css_score - prev_css) >= settings.alert_css_delta:
            if agg.css_score >= settings.alert_css_min or is_watched:
                rising.append((agg, prev_css))

        # 2. Crossed elite threshold for the first time
        if (prev_css is None or prev_css < settings.alert_css_min) and agg.css_score >= settings.alert_css_min:
            elite.append(agg)

        # 3. Watchlist delta (smaller threshold than rising star)
        if is_watched and prev_css is not None and abs(agg.css_score - prev_css) >= 2.0:
            watch_deltas.append((agg, prev_css))

    # 4. Win streaks — only on watchlist + elite-tier players (avoid spam)
    streak_targets = (
        db.query(Player, PlayerAggregate)
        .join(PlayerAggregate, PlayerAggregate.puuid == Player.puuid)
        .filter(PlayerAggregate.css_score >= settings.alert_css_min)
        .all()
    )
    for p, agg in streak_targets:
        results = _last_n_results(db, p.puuid, n=settings.alert_winrate_streak_min)
        streak = _detect_streak(results)
        if streak >= settings.alert_winrate_streak_min:
            win_streaks.append((p, agg, streak))

    # ---------- Compose + send ----------
    metas = {m.puuid: m for m in db.query(PlayerMeta).all()}

    def player_line(puuid: str, css: float, role: str, extra: str = "") -> str:
        p = db.get(Player, puuid)
        if not p:
            return f"puuid {puuid[:8]}"
        meta = metas.get(puuid)
        name = _format_player(p, meta)
        link = _player_link(puuid)
        return f"• **{name}** — {role} CSS **{css:.1f}**{extra}\n  <{link}>"

    if has_webhook:
        if rising:
            rising.sort(key=lambda x: x[0].css_score - x[1], reverse=True)
            lines = [
                player_line(a.puuid, a.css_score, a.role, f" (was {prev:.1f}, +{a.css_score - prev:+.1f})")
                for a, prev in rising[:8]
            ]
            msg = f"🚀 **Rising stars** ({len(rising)}):\n" + "\n".join(lines)
            sent += _send(msg)

        if elite:
            elite.sort(key=lambda a: a.css_score, reverse=True)
            lines = [player_line(a.puuid, a.css_score, a.role, " — first time crossing elite") for a in elite[:8]]
            msg = f"⭐ **Crossed CSS {settings.alert_css_min:.0f}** ({len(elite)}):\n" + "\n".join(lines)
            sent += _send(msg)

        if watch_deltas:
            watch_deltas.sort(key=lambda x: abs(x[0].css_score - x[1]), reverse=True)
            lines = [
                player_line(a.puuid, a.css_score, a.role, f" (Δ {a.css_score - prev:+.1f} from {prev:.1f})")
                for a, prev in watch_deltas[:10]
            ]
            msg = f"👁️ **Watchlist deltas** ({len(watch_deltas)}):\n" + "\n".join(lines)
            sent += _send(msg)

        if win_streaks:
            win_streaks.sort(key=lambda x: x[2], reverse=True)
            lines = [player_line(p.puuid, agg.css_score, agg.role, f" — {streak}W streak") for p, agg, streak in win_streaks[:6]]
            msg = f"🔥 **Hot win streaks** ({len(win_streaks)}):\n" + "\n".join(lines)
            sent += _send(msg)
    else:
        logger.info("alerts: webhooks not configured — skipping notifications, but saving snapshot for rising-star detector")

    # Persist snapshots after sending (so a webhook failure doesn't lose history)
    db.bulk_save_objects(new_snapshots)
    db.commit()

    logger.info("alerts: %d signals sent (rising=%d elite=%d watch=%d streaks=%d)",
                sent, len(rising), len(elite), len(watch_deltas), len(win_streaks))
    return sent


def send_test_alert() -> int:
    """Manually send a test ping to verify webhook config."""
    return _send("✅ **Challenger Scouting alerts test** — webhook is alive.")
