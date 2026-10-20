"""GitHub-Actions-safe publisher. No Windows paths, no R:\INSTA access —
reads each due row's `video_url` (set by migrate_videos_to_supabase.py)
instead of a local file path.

- YouTube and Facebook need real bytes on disk to upload, so this downloads
  video_url to a temp file first.
- Instagram's publish_instagram_reel() already accepts a plain URL directly
  — no download needed, no code change needed in social_publishers.py.

Runs from a repo checkout that also contains master_orchestrator.py /
social_publishers.py / topics_pipeline.db (committed to the repo — see
.github/workflows/rishi-publish.yml). Exits non-zero only on an unexpected
crash; individual row failures are logged and skipped, never fatal.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import master_orchestrator as mo  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("ci_runner")

YT_CAP = int(os.getenv("CI_YT_CAP", "8"))
IG_PACE = int(os.getenv("CI_IG_PACE", "60"))


def download(url: str, suffix: str = ".mp4") -> Path:
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        for chunk in resp.iter_content(1 << 20):
            fh.write(chunk)
    return Path(path)


def overdue_rows(con, id_field: str) -> list:
    now = datetime.now(timezone.utc)
    rows = con.execute(
        f"""
        SELECT * FROM episodes
        WHERE status IN ('pending','error','partial')
          AND {id_field} IS NULL
          AND scheduled_for IS NOT NULL
          AND video_url IS NOT NULL AND video_url != ''
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


def run_youtube(con) -> int:
    rows = overdue_rows(con, "yt_video_id")[:YT_CAP]
    done = 0
    for row in rows:
        try:
            local = download(row["video_url"])
            try:
                publish_at = datetime.fromisoformat(row["scheduled_for"])
                vid = mo.sp.schedule_youtube_video(
                    video_path=local, title=row["title"],
                    description=row["description"] or row["fb_caption"] or "",
                    tags=__import__("json").loads(row["tags"] or "[]"),
                    publish_at_utc=publish_at,
                )
                mo._record(con, row["id"], yt_video_id=vid, yt_status="scheduled")
                finish_row(con, row["id"])
                log.info("[YouTube] ep%s p%s -> %s", row["ep"], row["part"], vid)
                done += 1
            finally:
                local.unlink(missing_ok=True)
        except Exception as exc:
            mo._record(con, row["id"], yt_status="error", last_error=str(exc)[:900],
                       attempts=row["attempts"] + 1)
            log.error("[YouTube] ep%s p%s FAILED: %s", row["ep"], row["part"], str(exc)[:200])
    return done


def run_facebook(con) -> int:
    rows = overdue_rows(con, "fb_video_id")
    done = 0
    for row in rows:
        try:
            local = download(row["video_url"])
            try:
                publish_at = datetime.fromisoformat(row["scheduled_for"])
                res = mo.sp.schedule_facebook_reel(
                    video_path=local, caption=row["fb_caption"] or row["description"] or row["title"],
                    publish_at_utc=publish_at,
                )
                mo._record(con, row["id"], fb_video_id=res["video_id"], fb_status=res["state"])
                finish_row(con, row["id"])
                log.info("[Facebook] ep%s p%s -> %s", row["ep"], row["part"], res["video_id"])
                done += 1
            finally:
                local.unlink(missing_ok=True)
        except Exception as exc:
            mo._record(con, row["id"], fb_status="error", last_error=str(exc)[:900],
                       attempts=row["attempts"] + 1)
            log.error("[Facebook] ep%s p%s FAILED: %s", row["ep"], row["part"], str(exc)[:200])
        time.sleep(2)
    return done


def run_instagram(con) -> int:
    """Priority platform: clear the whole backlog, no cap. Uses video_url directly."""
    rows = overdue_rows(con, "ig_media_id")
    done = 0
    for i, row in enumerate(rows):
        try:
            media_id = mo.sp.publish_instagram_reel(
                video_path=row["video_url"],  # URL passthrough — no download
                caption=row["fb_caption"] or row["description"] or row["title"],
            )
            mo._record(con, row["id"], ig_media_id=media_id, ig_status="published")
            finish_row(con, row["id"])
            log.info("[Instagram] ep%s p%s -> %s", row["ep"], row["part"], media_id)
            done += 1
        except Exception as exc:
            mo._record(con, row["id"], ig_status="error", last_error=str(exc)[:900],
                       attempts=row["attempts"] + 1)
            log.error("[Instagram] ep%s p%s FAILED: %s", row["ep"], row["part"], str(exc)[:200])
        if i < len(rows) - 1:
            time.sleep(IG_PACE)
    return done


def main() -> int:
    con = mo.connect()
    mo.seed_database(con)
    readiness = mo.platform_readiness()
    yt = run_youtube(con) if readiness["youtube"][0] else 0
    fb = run_facebook(con) if readiness["facebook"][0] else 0
    ig = run_instagram(con) if readiness["instagram"][0] else 0
    ok, detail = mo.run_build_data()
    log.info("Website data: %s", "OK" if ok else f"FAILED: {detail[:200]}")
    log.info("SUMMARY: YouTube +%d Facebook +%d Instagram +%d", yt, fb, ig)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
