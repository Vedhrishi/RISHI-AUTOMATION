"""RISHI cockpit — the one command you run every 10-12 days.

Install: copy this file to R:\INSTA\sebi_fin_engine\rishi_cockpit.py
(next to master_orchestrator.py, social_publishers.py and .env).

What one run does, in order:
  1. Renews the Meta (Facebook/Instagram) token if it's getting close to
     expiry, so you never have to re-paste one from Graph API Explorer.
  2. Catches up EVERY overdue, unfinished episode part across all three
     platforms since your last run — nothing is silently skipped, however
     many days you were away. All 100 episodes already carry a full
     pre-planned `scheduled_for` calendar (seeded once, running through
     ~Jan 2027), so "catch up" just means: push anything whose date has
     arrived and hasn't been uploaded/posted yet. This script deliberately
     does NOT recompute or extend that calendar — an earlier draft of this
     script called the engine's --batch-schedule helper to "top up" extra
     days, but that helper assumes an incrementally-growing queue and will
     silently overwrite already-correct future dates with new, wrong ones
     when the whole queue is already pre-dated (verified and removed).
  3. Rebuilds the website's local data file (same step the old daily
     tasks did).
  4. Prints one clear report of what happened and what's still pending.

Instagram has NO native "publish later" API for Business/Creator accounts —
Meta's Graph API posts a Reel immediately when called. That means Instagram
posts land in a batch (paced a little apart, not instant back-to-back)
whenever you run this, rather than spread one-per-day like YouTube/Facebook.
If you want Instagram to post on its own daily rhythm regardless of whether
you've run this recently, keep the existing RISHI_Morning_Drop_Part1 /
RISHI_Evening_Drop_Part2 Windows scheduled tasks enabled too — they already
do that, running even when you're logged out, and this cockpit is simply
the safety net for whenever the laptop was off.

Usage
-----
  python rishi_cockpit.py --dry-run              Plan only. No API calls,
                                                  no writes. Always run this
                                                  first to see what would
                                                  happen.
  python rishi_cockpit.py                        Live run, default caps.
  python rishi_cockpit.py --yt-cap 8             Max YouTube uploads this
                                                  run (YouTube's daily quota
                                                  is roughly 10 uploads;
                                                  stay under it).
  python rishi_cockpit.py --ig-pace 60           Seconds between consecutive
                                                  Instagram posts in one run.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time as time_mod
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / ".env", override=True)

import master_orchestrator as mo  # noqa: E402
import social_publishers as sp  # noqa: E402

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
log = logging.getLogger("rishi.cockpit")
log.setLevel(logging.INFO)
for h in (
    logging.FileHandler(LOG_DIR / f"cockpit-{datetime.now():%Y%m%d}.log", encoding="utf-8"),
    logging.StreamHandler(sys.stdout),
):
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)

RENEW_WINDOW_DAYS = 20


# ---------------------------------------------------------------------------
# step 1: token health
# ---------------------------------------------------------------------------
def renew_meta_token_if_needed(dry_run: bool) -> None:
    import os

    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        log.error("[Token] META_ACCESS_TOKEN is empty; cannot check or renew.")
        return
    try:
        d = sp._graph_get(
            "debug_token",
            {"input_token": token, "access_token": f"{os.getenv('FB_APP_ID')}|{os.getenv('FB_APP_SECRET')}"},
        )["data"]
    except Exception as exc:
        log.warning("[Token] Could not check token: %s", str(exc)[:200])
        return
    if not d.get("is_valid"):
        log.error("[Token] META_ACCESS_TOKEN is INVALID. Generate a new one in Graph API "
                   "Explorer and paste it into .env — this cannot be fixed automatically.")
        return
    expires_at = d.get("expires_at", 0)
    days_left = (
        None if not expires_at
        else (datetime.fromtimestamp(expires_at, tz=timezone.utc) - datetime.now(timezone.utc)).days
    )
    log.info("[Token] valid, days_left=%s", days_left if days_left is not None else "no fixed expiry")
    if days_left is not None and days_left > RENEW_WINDOW_DAYS:
        return
    if dry_run:
        log.info("[Token] WOULD renew now (dry-run).")
        return
    try:
        info = sp.setup_meta_credentials()
        log.info("[Token] Renewed. page_id=%s ig_user_id=%s", info["page_id"], info["ig_user_id"])
    except Exception as exc:
        log.exception("[Token] Renewal failed: %s", exc)


# ---------------------------------------------------------------------------
# step 2: backlog catch-up
# ---------------------------------------------------------------------------
def overdue_rows(con, id_field: str) -> list:
    now = datetime.now(timezone.utc)
    rows = con.execute(
        f"""
        SELECT * FROM episodes
        WHERE status IN ('pending','error','partial')
          AND {id_field} IS NULL
          AND scheduled_for IS NOT NULL
        ORDER BY ep ASC, part ASC
        """
    ).fetchall()
    due = []
    for row in rows:
        try:
            when = datetime.fromisoformat(row["scheduled_for"])
        except ValueError:
            continue
        if when <= now:
            due.append(row)
    return due


def finish_row(con, row_id: int) -> None:
    row = con.execute("SELECT * FROM episodes WHERE id=?", (row_id,)).fetchone()
    complete = bool(row["yt_video_id"] and row["fb_video_id"] and row["ig_media_id"])
    mo._record(con, row_id, status="published" if complete else "partial")


def catch_up_youtube(con, cap: int, dry_run: bool) -> int:
    rows = overdue_rows(con, "yt_video_id")
    log.info("[YouTube] %d overdue part(s) pending; cap this run = %d", len(rows), cap)
    done = 0
    for row in rows[:cap]:
        video = Path(row["video_path"])
        if not video.exists():
            log.error("[YouTube] ep%s p%s: video file missing (%s) — skipped", row["ep"], row["part"], video)
            continue
        if dry_run:
            log.info("[YouTube] WOULD upload ep%s p%s", row["ep"], row["part"])
            continue
        try:
            publish_at = datetime.fromisoformat(row["scheduled_for"])
            vid = mo._publish_youtube(row, publish_at)
            mo._record(con, row["id"], yt_video_id=vid, yt_status="scheduled")
            finish_row(con, row["id"])
            log.info("[YouTube] ep%s p%s -> %s", row["ep"], row["part"], vid)
            done += 1
        except Exception as exc:
            msg = str(exc)
            mo._record(con, row["id"], yt_status="error", last_error=msg[:900], attempts=row["attempts"] + 1)
            log.error("[YouTube] ep%s p%s FAILED: %s", row["ep"], row["part"], msg[:200])
            if "quota" in msg.lower() or "exceeded" in msg.lower():
                log.warning("[YouTube] Looks like the daily quota was hit — stopping YouTube for this run.")
                break
    if len(rows) > cap and not dry_run:
        log.info("[YouTube] %d more overdue part(s) left for the next run.", len(rows) - cap)
    return done


def catch_up_facebook(con, cap: int, dry_run: bool) -> int:
    rows = overdue_rows(con, "fb_video_id")
    log.info("[Facebook] %d overdue part(s) pending; cap this run = %d", len(rows), cap)
    done = 0
    for row in rows[:cap]:
        video = Path(row["video_path"])
        if not video.exists():
            log.error("[Facebook] ep%s p%s: video file missing (%s) — skipped", row["ep"], row["part"], video)
            continue
        if dry_run:
            log.info("[Facebook] WOULD schedule ep%s p%s", row["ep"], row["part"])
            continue
        try:
            publish_at = datetime.fromisoformat(row["scheduled_for"])
            res = mo._publish_facebook(row, publish_at)
            mo._record(con, row["id"], fb_video_id=res["video_id"], fb_status=res["state"])
            finish_row(con, row["id"])
            log.info("[Facebook] ep%s p%s -> %s (%s)", row["ep"], row["part"], res["video_id"], res["state"])
            done += 1
        except Exception as exc:
            mo._record(con, row["id"], fb_status="error", last_error=str(exc)[:900], attempts=row["attempts"] + 1)
            log.error("[Facebook] ep%s p%s FAILED: %s", row["ep"], row["part"], str(exc)[:200])
        time_mod.sleep(2)
    if len(rows) > cap and not dry_run:
        log.info("[Facebook] %d more overdue part(s) left for the next run.", len(rows) - cap)
    return done


def catch_up_instagram(con, pace_seconds: int, dry_run: bool) -> int:
    """High priority: clear the whole Instagram backlog every run, not just a cap."""
    rows = overdue_rows(con, "ig_media_id")
    log.info("[Instagram] %d overdue part(s) pending — clearing all of them.", len(rows))
    done = 0
    for i, row in enumerate(rows):
        video = Path(row["video_path"])
        if not video.exists():
            log.error("[Instagram] ep%s p%s: video file missing (%s) — skipped", row["ep"], row["part"], video)
            continue
        if dry_run:
            log.info("[Instagram] WOULD publish ep%s p%s", row["ep"], row["part"])
            continue
        try:
            media_id = mo._publish_instagram(row)
            mo._record(con, row["id"], ig_media_id=media_id, ig_status="published")
            finish_row(con, row["id"])
            log.info("[Instagram] ep%s p%s -> %s", row["ep"], row["part"], media_id)
            done += 1
        except Exception as exc:
            mo._record(con, row["id"], ig_status="error", last_error=str(exc)[:900], attempts=row["attempts"] + 1)
            log.error("[Instagram] ep%s p%s FAILED: %s", row["ep"], row["part"], str(exc)[:200])
        if i < len(rows) - 1:
            time_mod.sleep(pace_seconds)
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yt-cap", type=int, default=8, help="max YouTube uploads this run")
    ap.add_argument("--fb-cap", type=int, default=40, help="max Facebook schedules this run")
    ap.add_argument("--ig-pace", type=int, default=60, help="seconds between Instagram posts")
    args = ap.parse_args()

    log.info("=" * 70)
    log.info("RISHI COCKPIT RUN  dry_run=%s", args.dry_run)
    log.info("=" * 70)

    renew_meta_token_if_needed(args.dry_run)

    readiness = mo.platform_readiness()
    for p in mo.PLATFORMS:
        ready, missing = readiness[p]
        log.info("[Readiness] %-10s ready=%s %s", p, ready, ("missing=" + ",".join(missing)) if missing else "")

    con = mo.connect()
    mo.seed_database(con)

    yt_done = catch_up_youtube(con, args.yt_cap, args.dry_run) if readiness["youtube"][0] else 0
    fb_done = catch_up_facebook(con, args.fb_cap, args.dry_run) if readiness["facebook"][0] else 0
    ig_done = catch_up_instagram(con, args.ig_pace, args.dry_run) if readiness["instagram"][0] else 0

    if not args.dry_run:
        ok, detail = mo.run_build_data()
        log.info("[Website data] %s — %s", "OK" if ok else "FAILED", detail[:300])

    total = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    published = con.execute("SELECT COUNT(*) FROM episodes WHERE status='published'").fetchone()[0]
    remaining = total - published
    log.info("=" * 70)
    log.info("SUMMARY: YouTube +%d  Facebook +%d  Instagram +%d this run", yt_done, fb_done, ig_done)
    log.info("Queue: %d/%d fully published, %d remaining", published, total, remaining)
    log.info("=" * 70)
    con.close()
    return 0


if __name__ == "__main__":
    import os
    os.environ.setdefault("RISHI_NONINTERACTIVE", "1")
    sys.exit(main())
