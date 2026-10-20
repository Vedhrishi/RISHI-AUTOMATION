"""RISHI master orchestrator.

Pulls the next episode from topics_pipeline.db, schedules it on YouTube and
Facebook Reels, publishes it on Instagram, writes every remote id back to
SQLite, and rebuilds the website data.

Usage
-----
  python sebi_fin_engine/master_orchestrator.py --slot morning [--dry-run]
  python sebi_fin_engine/master_orchestrator.py --slot evening [--dry-run]
  python sebi_fin_engine/master_orchestrator.py --status
  python sebi_fin_engine/master_orchestrator.py --auth-youtube
  python sebi_fin_engine/master_orchestrator.py --setup-meta

Slot semantics: morning -> part 1, evening -> part 2.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402

ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=True)

import social_publishers as sp  # noqa: E402

SLOT_PART = {"morning": 1, "evening": 2}
SLOT_UTC = {"morning": (1, 30), "evening": (13, 30)}
TASKS = {
    "morning": "RISHI_Morning_Drop_Part1",
    "evening": "RISHI_Evening_Drop_Part2",
}
PLATFORMS = ("youtube", "facebook", "instagram")

log = logging.getLogger("rishi.orchestrator")


# ---------------------------------------------------------------------------
# paths / logging
# ---------------------------------------------------------------------------
def _path(env_key: str, default: Path) -> Path:
    raw = os.getenv(env_key)
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else (BASE_DIR / p)


DB_PATH = _path("DB_PATH", BASE_DIR / "topics_pipeline.db")
VIDEO_DIR = _path("VIDEO_DIR", ROOT / "output")
BUILD_DATA = _path("BUILD_DATA_SCRIPT", ROOT / "website" / "tools" / "build_data.py")
LOG_DIR = _path("LOG_DIR", BASE_DIR / "logs")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    handler_file = logging.FileHandler(
        LOG_DIR / f"orchestrator-{datetime.now():%Y%m%d}.log", encoding="utf-8"
    )
    handler_console = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (handler_file, handler_console):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[handler_file, handler_console])


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ep INTEGER NOT NULL,
    part INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    tags TEXT,
    fb_caption TEXT,
    video_path TEXT,
    scheduled_for TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    yt_video_id TEXT,
    yt_status TEXT,
    fb_video_id TEXT,
    fb_status TEXT,
    ig_media_id TEXT,
    ig_status TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ep, part)
);
CREATE INDEX IF NOT EXISTS idx_episodes_queue ON episodes (part, status, ep);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    episode_id INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    detail TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def _yt_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:shorts/|youtu\.be/|watch\?v=)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _tags_from_caption(caption: str) -> list[str]:
    tags = re.findall(r"#(\w+)", caption or "")
    return [t for t in dict.fromkeys(tags) if t.lower() not in ("indianstocksexplained", "shorts")]


def seed_database(con: sqlite3.Connection) -> int:
    """Populate the queue from existing YouTube/Facebook metadata exactly once."""
    count = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    if count:
        return 0

    yt_file = ROOT / "youtube" / "captions" / "youtube_meta.json"
    fb_file = ROOT / "facebook" / "captions" / "facebook_meta.json"
    status_file = ROOT / "youtube" / "schedule" / "yt_status.json"
    cap_file = ROOT / "series_cap.json"

    if not yt_file.exists():
        log.warning("No metadata at %s; queue left empty", yt_file)
        return 0

    yt_meta = json.loads(yt_file.read_text(encoding="utf-8"))
    fb_meta = (
        json.loads(fb_file.read_text(encoding="utf-8")) if fb_file.exists() else {}
    )
    yt_status = (
        json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {}
    )
    cap = 10_000
    if cap_file.exists():
        cap = int(json.loads(cap_file.read_text(encoding="utf-8")).get("last_ep", cap))

    inserted = 0
    for key, meta in yt_meta.items():
        ep = int(meta["ep"])
        part = int(meta["part"])
        if ep > cap:
            continue
        video = VIDEO_DIR / meta["file"]
        fb_row = fb_meta.get(key, {})
        caption = fb_row.get("caption") or meta.get("description", "")
        published_url = yt_status.get(key, {}).get("url")
        yt_id = _yt_id_from_url(published_url)
        status = "published" if yt_id else "pending"

        con.execute(
            """
            INSERT OR IGNORE INTO episodes
                (ep, part, title, description, tags, fb_caption, video_path,
                 scheduled_for, status, yt_video_id, yt_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ep,
                part,
                meta.get("title", f"Episode {ep} Part {part}"),
                meta.get("description", ""),
                json.dumps(_tags_from_caption(caption)),
                caption,
                str(video).replace("\\", "/"),
                None,  # scheduled_for is engine state, not legacy metadata
                status,
                yt_id,
                "uploaded" if yt_id else None,
            ),
        )
        inserted += 1
    con.commit()
    log.info("Seeded %d episodes into %s", inserted, DB_PATH)
    return inserted


# ---------------------------------------------------------------------------
# queue / timing
# ---------------------------------------------------------------------------
def queue_counts(con: sqlite3.Connection) -> dict[int, int]:
    rows = con.execute(
        "SELECT part, COUNT(*) FROM episodes WHERE status IN ('pending','error','partial') GROUP BY part"
    ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def pick_next(con: sqlite3.Connection, part: int) -> sqlite3.Row | None:
    """Oldest incomplete row for this part, but only if it is due today."""
    row = con.execute(
        """
        SELECT * FROM episodes
        WHERE part = ?
          AND status IN ('pending','error','partial')
          AND (yt_video_id IS NULL OR fb_video_id IS NULL OR ig_media_id IS NULL)
        ORDER BY ep ASC, part ASC
        LIMIT 1
        """,
        (part,),
    ).fetchone()
    if row is None or not row["scheduled_for"]:
        return row
    try:
        due = datetime.fromisoformat(row["scheduled_for"]).astimezone(timezone.utc).date()
    except ValueError:
        return row
    if due > datetime.now(timezone.utc).date():
        log.info(
            "Next row (ep %d part %d) is batch-scheduled for %s; nothing due today.",
            row["ep"], row["part"], due,
        )
        return None
    return row


def slot_publish_at(slot: str, now: datetime | None = None) -> datetime:
    """Today's slot in UTC; rolls to tomorrow if the lead time is too short."""
    now = now or datetime.now(timezone.utc)
    hour, minute = SLOT_UTC[slot]
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    lead = int(os.getenv("MIN_PUBLISH_LEAD_MINUTES", "5"))
    if target <= now + timedelta(minutes=lead):
        target += timedelta(days=1)
    return target


def platform_readiness() -> dict[str, tuple[bool, list[str]]]:
    caps = sp.credentials_status()
    youtube = caps["youtube_token"]
    facebook = caps["fb_page_id"] and caps["fb_page_token"]
    instagram = (
        caps["ig_user_id"]
        and caps["fb_page_token"]
        and caps["supabase_url"]
        and caps["supabase_service_key"]
    )
    missing = {
        "youtube": [] if youtube else ["credentials/yt_token.json"],
        "facebook": []
        if facebook
        else [k for k in ("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN") if not caps[_cap_key(k)]],
        "instagram": []
        if instagram
        else [
            k
            for k in ("IG_USER_ID", "FB_PAGE_ACCESS_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_KEY")
            if not caps[_cap_key(k)]
        ],
    }
    return {
        "youtube": (youtube, missing["youtube"]),
        "facebook": (facebook, missing["facebook"]),
        "instagram": (instagram, missing["instagram"]),
    }


def _cap_key(env_key: str) -> str:
    return {
        "FB_PAGE_ID": "fb_page_id",
        "FB_PAGE_ACCESS_TOKEN": "fb_page_token",
        "IG_USER_ID": "ig_user_id",
        "SUPABASE_URL": "supabase_url",
        "SUPABASE_SERVICE_KEY": "supabase_service_key",
    }[env_key]


def task_status(name: str) -> str:
    if os.name != "nt":
        return "n/a (non-Windows)"
    try:
        res = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:  # pragma: no cover
        return f"query failed ({exc})"
    if res.returncode != 0:
        return "NOT REGISTERED"
    for line in res.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip() or "registered"
    return "registered"


# ---------------------------------------------------------------------------
# publishing steps
# ---------------------------------------------------------------------------
def _record(con: sqlite3.Connection, row_id: int, **fields: Any) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE episodes SET {cols} WHERE id = ?", (*fields.values(), row_id))
    con.commit()


def _publish_youtube(row: sqlite3.Row, publish_at: datetime) -> str:
    tags = json.loads(row["tags"] or "[]")
    return sp.schedule_youtube_video(
        video_path=Path(row["video_path"]),
        title=row["title"],
        description=row["description"] or row["fb_caption"] or "",
        tags=tags,
        publish_at_utc=publish_at,
    )


def _publish_facebook(row: sqlite3.Row, publish_at: datetime) -> dict[str, Any]:
    return sp.schedule_facebook_reel(
        video_path=Path(row["video_path"]),
        caption=row["fb_caption"] or row["description"] or row["title"],
        publish_at_utc=publish_at,
    )


def _publish_instagram(row: sqlite3.Row) -> str:
    return sp.publish_instagram_reel(
        video_path=Path(row["video_path"]),
        caption=row["fb_caption"] or row["description"] or row["title"],
    )


def run_build_data() -> tuple[bool, str]:
    if not BUILD_DATA.exists():
        return False, f"build_data.py missing at {BUILD_DATA}"
    try:
        res = subprocess.run(
            [sys.executable, str(BUILD_DATA)],
            cwd=str(BUILD_DATA.parent.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "build_data.py timed out after 600s"
    detail = (res.stdout or "").strip() or (res.stderr or "").strip()
    return res.returncode == 0, detail[-1500:]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def print_report(con: sqlite3.Connection, slot: str | None = None) -> None:
    caps = sp.credentials_status()
    counts = queue_counts(con)
    total = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    published = con.execute(
        "SELECT COUNT(*) FROM episodes WHERE status = 'published'"
    ).fetchone()[0]
    errored = con.execute(
        "SELECT COUNT(*) FROM episodes WHERE status IN ('error','partial')"
    ).fetchone()[0]

    def mark(ok: bool) -> str:
        return "OK" if ok else "MISSING"

    lines = [
        "",
        "=" * 66,
        " RISHI ENGINE STATUS".ljust(65) + "=",
        "=" * 66,
        " CREDENTIALS",
        f"   YouTube OAuth token ......... {mark(caps['youtube_token'])}",
        f"   YouTube client_secret.json .. {mark(caps['youtube_client_secret'])}",
        f"   Meta user token ............. {mark(caps['meta_user_token'])}",
        f"   FB Page Access Token ........ {mark(caps['fb_page_token'])}",
        f"   FB Page ID .................. {mark(caps['fb_page_id'])}",
        f"   IG User ID .................. {mark(caps['ig_user_id'])}",
        f"   Supabase URL ................ {mark(caps['supabase_url'])}",
        f"   Supabase service key ........ {mark(caps['supabase_service_key'])}",
        "",
        " QUEUE (topics_pipeline.db)",
        f"   Total episodes .............. {total}",
        f"   Published ................... {published}",
        f"   Errored / partial ........... {errored}",
        f"   Morning queue (part 1) ...... {counts.get(1, 0)}",
        f"   Evening queue (part 2) ...... {counts.get(2, 0)}",
        f"   Selected slot ............... {slot or '-'}",
        "",
        " SCHEDULED TASKS",
    ]
    for name in TASKS.values():
        lines.append(f"   {name:<29} {task_status(name)}")
    lines.append("=" * 66)
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# command: single slot
# ---------------------------------------------------------------------------
def cmd_run(slot: str, dry_run: bool) -> int:
    part = SLOT_PART[slot]
    con = connect()
    seed_database(con)
    row = pick_next(con, part)

    now = datetime.now(timezone.utc)
    publish_at = slot_publish_at(slot, now)
    readiness = platform_readiness()

    log.info("Slot=%s part=%d publish_at=%s dry_run=%s", slot, part,
             publish_at.strftime("%Y-%m-%dT%H:%M:%SZ"), dry_run)

    if row is None:
        log.warning("Queue is empty for part %d — nothing to do.", part)
        print_report(con, slot)
        return 0

    video = Path(row["video_path"])
    log.info("Selected ep %d part %d: %s", row["ep"], row["part"], row["title"])
    log.info("Video: %s (%s)", video, f"{video.stat().st_size / 1e6:.1f} MB" if video.exists() else "MISSING")

    missing_video = not video.exists()
    id_fields = {"youtube": "yt_video_id", "facebook": "fb_video_id", "instagram": "ig_media_id"}
    plan: dict[str, str] = {}
    for platform in PLATFORMS:
        ready, missing = readiness[platform]
        already = row[id_fields[platform]]
        if already:
            plan[platform] = f"SKIP (already {already})"
        elif missing_video:
            plan[platform] = "BLOCKED (video file missing)"
        elif not ready:
            plan[platform] = "BLOCKED (missing " + ", ".join(missing) + ")"
        else:
            plan[platform] = "WOULD SCHEDULE" if platform != "instagram" else "WOULD PUBLISH"
        log.info("  %-10s %s", platform, plan[platform])

    if dry_run:
        log.info("DRY RUN — no API calls, no episode status changes, build_data.py skipped.")
        print_report(con, slot)
        con.close()
        return 0

    ready_platforms = [p for p in PLATFORMS if readiness[p][0]]
    if not ready_platforms:
        log.error(
            "No platform has usable credentials; refusing a live run. "
            "Fill sebi_fin_engine/.env or run --auth-youtube / --setup-meta."
        )
        print_report(con, slot)
        con.close()
        return 2

    started = now.isoformat(timespec="seconds")
    run_id = con.execute(
        "INSERT INTO runs (slot, dry_run, episode_id, started_at) VALUES (?, 0, ?, ?)",
        (slot, row["id"], started),
    ).lastrowid
    con.commit()

    errors: list[str] = []
    attempted = 0

    # --- YouTube ---
    if not row["yt_video_id"] and readiness["youtube"][0]:
        attempted += 1
        try:
            vid = _publish_youtube(row, publish_at)
            _record(con, row["id"], yt_video_id=vid, yt_status="scheduled",
                    scheduled_for=publish_at.isoformat(timespec="seconds"))
            row = con.execute("SELECT * FROM episodes WHERE id=?", (row["id"],)).fetchone()
            log.info("[YouTube] OK %s", vid)
        except Exception as exc:
            errors.append(f"youtube: {exc}")
            _record(con, row["id"], yt_status="error", last_error=str(exc)[:900],
                    attempts=row["attempts"] + 1)
            log.exception("[YouTube] FAILED")

    # --- Facebook ---
    if not row["fb_video_id"] and readiness["facebook"][0]:
        attempted += 1
        try:
            res = _publish_facebook(row, publish_at)
            _record(con, row["id"], fb_video_id=res["video_id"], fb_status=res["state"],
                    scheduled_for=publish_at.isoformat(timespec="seconds"))
            row = con.execute("SELECT * FROM episodes WHERE id=?", (row["id"],)).fetchone()
            log.info("[Facebook] OK %s (%s)", res["video_id"], res["state"])
        except Exception as exc:
            errors.append(f"facebook: {exc}")
            _record(con, row["id"], fb_status="error", last_error=str(exc)[:900],
                    attempts=row["attempts"] + 1)
            log.exception("[Facebook] FAILED")

    # --- Instagram ---
    if not row["ig_media_id"] and readiness["instagram"][0]:
        attempted += 1
        try:
            media_id = _publish_instagram(row)
            _record(con, row["id"], ig_media_id=media_id, ig_status="published")
            row = con.execute("SELECT * FROM episodes WHERE id=?", (row["id"],)).fetchone()
            log.info("[Instagram] OK %s", media_id)
        except Exception as exc:
            errors.append(f"instagram: {exc}")
            _record(con, row["id"], ig_status="error", last_error=str(exc)[:900],
                    attempts=row["attempts"] + 1)
            log.exception("[Instagram] FAILED")

    complete = bool(row["yt_video_id"] and row["fb_video_id"] and row["ig_media_id"])
    status = "published" if complete else ("partial" if attempted else "error")
    _record(con, row["id"], status=status, last_error=("; ".join(errors)[:900] if errors else None))

    build_ok, build_detail = run_build_data()
    log.info("build_data.py: %s — %s", "OK" if build_ok else "FAILED", build_detail)

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = not errors and build_ok
    con.execute(
        "UPDATE runs SET finished_at=?, ok=?, detail=? WHERE id=?",
        (finished, int(ok), ("; ".join(errors) or build_detail)[:2000], run_id),
    )
    con.commit()

    print_report(con, slot)
    con.close()
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 14-day batch scheduling
# ---------------------------------------------------------------------------
def select_batch_pairs(con: sqlite3.Connection, days: int) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    """Next N episodes with both parts unscheduled and both MP4s on disk."""
    candidates = con.execute(
        """
        SELECT e1.id AS id1, e2.id AS id2, e1.ep AS ep
        FROM episodes e1
        JOIN episodes e2 ON e2.ep = e1.ep AND e2.part = 2
        WHERE e1.part = 1
          AND e1.status IN ('pending','error','partial')
          AND e2.status IN ('pending','error','partial')
          AND e1.yt_video_id IS NULL AND e1.fb_video_id IS NULL
          AND e2.yt_video_id IS NULL AND e2.fb_video_id IS NULL
        ORDER BY e1.ep
        LIMIT ?
        """,
        (days * 8,),
    ).fetchall()

    pairs: list[tuple[sqlite3.Row, sqlite3.Row]] = []
    for cand in candidates:
        part1 = con.execute("SELECT * FROM episodes WHERE id=?", (cand["id1"],)).fetchone()
        part2 = con.execute("SELECT * FROM episodes WHERE id=?", (cand["id2"],)).fetchone()
        if Path(part1["video_path"]).exists() and Path(part2["video_path"]).exists():
            pairs.append((part1, part2))
        if len(pairs) >= days:
            break
    return pairs


def print_batch_table(con: sqlite3.Connection, schedule: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 94)
    print(
        f"{'DATE (IST)':<13}{'TIME':<7}{'EP':>4}{'PART':>6}  "
        f"{'YOUTUBE':<18}{'FACEBOOK':<18}{'INSTAGRAM':<16}"
    )
    print("-" * 94)
    for item in schedule:
        row = con.execute("SELECT * FROM episodes WHERE id=?", (item["id"],)).fetchone()
        yt = row["yt_video_id"] or "-"
        fb = row["fb_video_id"] or "-"
        ig = row["ig_media_id"] or "DEFERRED"
        print(
            f"{item['date'].isoformat():<13}{item['ist']:<7}{item['ep']:>4}{item['part']:>6}  "
            f"{yt:<18}{fb:<18}{ig:<16}"
        )
    print("=" * 94)


def cmd_batch(days: int, dry_run: bool, start: str | None) -> int:
    con = connect()
    seed_database(con)
    pairs = select_batch_pairs(con, days)
    if not pairs:
        log.warning("No fully-rendered unscheduled episode pairs remain.")
        con.close()
        return 0
    if len(pairs) < days:
        log.warning("Only %d of %d episodes are available for batch scheduling.", len(pairs), days)

    if start:
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            log.error("--start must be YYYY-MM-DD")
            con.close()
            return 2
    else:
        ist = timezone(timedelta(hours=5, minutes=30))
        start_date = (datetime.now(ist) + timedelta(days=1)).date()
        # Resume after any existing future schedule instead of overlapping it.
        latest = con.execute(
            """
            SELECT MAX(date(scheduled_for)) FROM episodes
            WHERE scheduled_for IS NOT NULL AND date(scheduled_for) >= date('now')
            """
        ).fetchone()[0]
        if latest:
            horizon = datetime.strptime(latest, "%Y-%m-%d").date() + timedelta(days=1)
            start_date = max(start_date, horizon)

    schedule: list[dict[str, Any]] = []
    for offset, (part1, part2) in enumerate(pairs):
        day = start_date + timedelta(days=offset)
        for part, part_row, hour, minute, label in (
            (1, part1, 1, 30, "07:00"),
            (2, part2, 13, 30, "19:00"),
        ):
            schedule.append(
                {
                    "date": day,
                    "ist": label,
                    "part": part,
                    "ep": part_row["ep"],
                    "id": part_row["id"],
                    "publish_at": datetime.combine(day, time(hour, minute), tzinfo=timezone.utc),
                }
            )

    readiness = platform_readiness()
    log.info(
        "Batch: %d episodes / %d slots, %s .. %s (IST), dry_run=%s",
        len(pairs), len(schedule), start_date, schedule[-1]["date"], dry_run,
    )
    log.info(
        "YouTube ready=%s  Facebook ready=%s  Instagram=native API unsupported (deferred)",
        readiness["youtube"][0], readiness["facebook"][0],
    )

    if dry_run:
        print_batch_table(con, schedule)
        print(
            f"PLAN ONLY: {len(schedule)} slots across {len(pairs)} episodes. "
            "No API calls; Instagram stays deferred to the daily 06:50/18:50 tasks."
        )
        con.close()
        return 0

    if not (readiness["youtube"][0] or readiness["facebook"][0]):
        log.error(
            "No YouTube or Facebook credentials available; refusing a live batch. "
            "Run --auth-youtube / --setup-meta first."
        )
        print_batch_table(con, schedule)
        con.close()
        return 2

    errors: list[str] = []
    for item in schedule:
        row = con.execute("SELECT * FROM episodes WHERE id=?", (item["id"],)).fetchone()
        publish_at = item["publish_at"]

        if readiness["youtube"][0] and not row["yt_video_id"]:
            try:
                vid = _publish_youtube(row, publish_at)
                _record(con, row["id"], yt_video_id=vid, yt_status="scheduled")
                log.info("[Batch][YouTube] ep %d p%d -> %s", row["ep"], row["part"], vid)
            except Exception as exc:
                errors.append(f"ep{row['ep']}p{row['part']} youtube: {exc}")
                _record(con, row["id"], yt_status="error", last_error=str(exc)[:900],
                        attempts=row["attempts"] + 1)
                log.exception("[Batch][YouTube] ep %d p%d FAILED", row["ep"], row["part"])

        if readiness["facebook"][0] and not row["fb_video_id"]:
            try:
                res = _publish_facebook(row, publish_at)
                _record(con, row["id"], fb_video_id=res["video_id"], fb_status=res["state"])
                log.info("[Batch][Facebook] ep %d p%d -> %s (%s)",
                         row["ep"], row["part"], res["video_id"], res["state"])
            except Exception as exc:
                errors.append(f"ep{row['ep']}p{row['part']} facebook: {exc}")
                _record(con, row["id"], fb_status="error", last_error=str(exc)[:900],
                        attempts=row["attempts"] + 1)
                log.exception("[Batch][Facebook] ep %d p%d FAILED", row["ep"], row["part"])

        # Instagram Content Publishing has no native scheduling. Leave the row
        # due on its date; the 06:50/18:50 task publishes the reel then.
        _record(
            con,
            row["id"],
            scheduled_for=publish_at.isoformat(timespec="seconds"),
            ig_status=row["ig_status"] or "deferred",
            status="partial",
        )

    print_batch_table(con, schedule)
    print(
        f"BATCH {'COMPLETE' if not errors else 'WITH ERRORS'}: {len(schedule)} slots, "
        f"{start_date} .. {schedule[-1]['date']}. Instagram reels remain deferred."
    )
    for err in errors:
        log.error("  %s", err)
    con.close()
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RISHI master publishing orchestrator")
    parser.add_argument("--slot", choices=sorted(SLOT_PART), help="morning (part 1) or evening (part 2)")
    parser.add_argument("--dry-run", action="store_true", help="plan only; no API calls or writes")
    parser.add_argument("--status", action="store_true", help="print credentials/queue/tasks and exit")
    parser.add_argument("--auth-youtube", action="store_true", help="run one-time YouTube OAuth flow")
    parser.add_argument("--setup-meta", action="store_true", help="exchange Meta token and resolve page/IG ids")
    parser.add_argument("--setup-supabase", action="store_true", help="prompt for Supabase URL and service key")
    parser.add_argument("--batch-schedule", action="store_true", help="schedule the next N days (default 14)")
    parser.add_argument("--days", type=int, default=14, help="days to batch schedule (default 14)")
    parser.add_argument("--start", help="first batch date YYYY-MM-DD (default: tomorrow IST)")
    args = parser.parse_args(argv)

    setup_logging()

    try:
        if args.auth_youtube:
            path = sp.run_youtube_oauth_flow()
            info = sp.verify_youtube_token()
            print(f"YouTube token written to {path}")
            print(f"Channel: {info['channel_title']} ({info['channel_id']})")
            return 0
        if args.setup_meta:
            info = sp.setup_meta_credentials()
            print(json.dumps(info, indent=2))
            print("Meta credentials written to sebi_fin_engine/.env")
            return 0
        if args.setup_supabase:
            info = sp.setup_supabase_credentials()
            print(json.dumps(info, indent=2))
            print("Supabase credentials written to sebi_fin_engine/.env")
            return 0
        if args.batch_schedule:
            return cmd_batch(args.days, args.dry_run, args.start)
        if args.status:
            con = connect()
            seed_database(con)
            print_report(con, None)
            con.close()
            return 0
        if not args.slot:
            parser.error("--slot is required unless --status/--auth-youtube/--setup-meta is used")
        return cmd_run(args.slot, args.dry_run)
    except Exception as exc:
        log.exception("Orchestrator fatal error")
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
