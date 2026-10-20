"""One-time migration: host every not-yet-fully-published episode video in a
PERMANENT Supabase Storage bucket, so GitHub Actions (which has no access to
R:\INSTA) can publish them without needing the laptop at all.

Deliberately separate from the existing 'reels-staging' bucket, which the
engine already uses as transient Instagram-upload scratch space — this one
is meant to hold files indefinitely.

Adds a `video_url` column to the episodes table if it doesn't exist yet, and
fills it in for every row that still needs any platform posted.

Usage
-----
  python migrate_videos_to_supabase.py --dry-run     List what would upload,
                                                      no network calls.
  python migrate_videos_to_supabase.py --apply       Actually upload + write
                                                      video_url into the DB.

Run this from the laptop (it needs read access to R:\INSTA\output). Install
next to master_orchestrator.py first.
"""
from __future__ import annotations

import argparse
import mimetypes
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / ".env", override=True)

import master_orchestrator as mo  # noqa: E402
import social_publishers as sp  # noqa: E402

ARCHIVE_BUCKET = "rishi-archive"


def ensure_column(con) -> None:
    cols = [r[1] for r in con.execute("PRAGMA table_info(episodes)").fetchall()]
    if "video_url" not in cols:
        con.execute("ALTER TABLE episodes ADD COLUMN video_url TEXT")
        con.commit()
        print("Added video_url column.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually upload (default is dry-run)")
    args = ap.parse_args()

    con = mo.connect()
    ensure_column(con)

    rows = con.execute(
        """
        SELECT * FROM episodes
        WHERE status != 'published' AND (video_url IS NULL OR video_url = '')
        ORDER BY ep, part
        """
    ).fetchall()

    print(f"{len(rows)} row(s) need a permanent hosted URL.")
    if not args.apply:
        total_mb = 0.0
        for row in rows:
            p = Path(row["video_path"])
            if p.exists():
                total_mb += p.stat().st_size / 1e6
        print(f"DRY RUN — would upload ~{total_mb:.0f} MB to Supabase bucket '{ARCHIVE_BUCKET}'.")
        for row in rows[:5]:
            print(f"  ep{row['ep']} p{row['part']}: {row['video_path']}")
        if len(rows) > 5:
            print(f"  ... and {len(rows) - 5} more")
        return 0

    client = sp._supabase_client()
    storage = client.storage
    try:
        storage.create_bucket(ARCHIVE_BUCKET, options={"public": True})
        print(f"Created bucket {ARCHIVE_BUCKET}")
    except Exception:
        pass  # already exists

    done, failed = 0, []
    for row in rows:
        p = Path(row["video_path"])
        if not p.exists():
            print(f"MISSING file, skipped: ep{row['ep']} p{row['part']} -> {p}")
            continue
        object_path = f"episodes/{p.name}"
        mime = mimetypes.guess_type(p.name)[0] or "video/mp4"
        data = p.open("rb").read()
        for attempt in range(1, 4):  # transient network/server errors happen over a long batch
            try:
                storage.from_(ARCHIVE_BUCKET).upload(
                    object_path, data, {"content-type": mime, "upsert": "true"}
                )
                break
            except Exception as exc:
                if attempt == 3:
                    print(f"FAILED after 3 tries: ep{row['ep']} p{row['part']}: {str(exc)[:150]}")
                    failed.append((row["ep"], row["part"]))
                    data = None
                else:
                    wait = 5 * attempt
                    print(f"  retry {attempt}/3 for ep{row['ep']} p{row['part']} in {wait}s: {str(exc)[:100]}")
                    time.sleep(wait)
        if data is None:
            continue
        url = storage.from_(ARCHIVE_BUCKET).get_public_url(object_path)
        mo._record(con, row["id"], video_url=url)
        print(f"ep{row['ep']} p{row['part']} -> {url}")
        done += 1
    if failed:
        print(f"\n{len(failed)} file(s) failed after retries (re-run this script to retry them): {failed}")

    print(f"Uploaded {done}/{len(rows)}.")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
