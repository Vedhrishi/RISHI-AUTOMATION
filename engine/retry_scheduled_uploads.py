"""Fill in missing YouTube uploads for rows that are already batch-scheduled.

The 14-day batch hit YouTube's per-channel daily upload ceiling (≈10 videos)
after ep6–ep10. This script re-uploads the remaining rows that already carry a
`scheduled_for` timestamp but no `yt_video_id`, so they land on the channel at
their intended times instead of being published live by the daily slots.

Usage
-----
  python sebi_fin_engine/retry_scheduled_uploads.py --dry-run
  python sebi_fin_engine/retry_scheduled_uploads.py --limit 10
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import master_orchestrator as mo  # noqa: E402


def pending_rows(limit: int) -> list:
    con = mo.connect()
    rows = con.execute(
        """
        SELECT * FROM episodes
        WHERE scheduled_for IS NOT NULL
          AND yt_video_id IS NULL
          AND date(scheduled_for) >= date('now')
        ORDER BY scheduled_for
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return con, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry missing scheduled YouTube uploads")
    parser.add_argument("--limit", type=int, default=10, help="max uploads this run (default 10)")
    parser.add_argument("--dry-run", action="store_true", help="list only")
    args = parser.parse_args()

    mo.setup_logging()
    con, rows = pending_rows(args.limit)
    if not rows:
        print("Nothing pending: every scheduled row already has a YouTube id.")
        con.close()
        return 0

    print(f"{'scheduled_for':<22}{'ep':>4}{'p':>3}  video")
    for row in rows:
        exists = Path(row["video_path"]).exists()
        print(f"{row['scheduled_for']:<22}{row['ep']:>4}{row['part']:>3}  "
              f"{Path(row['video_path']).name}{'' if exists else '  [MISSING]'}")
    if args.dry_run:
        print(f"PLAN ONLY: {len(rows)} uploads.")
        con.close()
        return 0

    con.close()
    failures = 0
    for row in rows:
        con = mo.connect()
        publish_at = datetime.fromisoformat(row["scheduled_for"]).astimezone(timezone.utc)
        try:
            vid = mo._publish_youtube(row, publish_at)
            mo._record(con, row["id"], yt_video_id=vid, yt_status="scheduled", last_error=None)
            print(f"OK   ep{row['ep']}p{row['part']} -> {vid} @ {publish_at:%Y-%m-%dT%H:%MZ}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            mo._record(con, row["id"], yt_status="error", last_error=str(exc)[:900],
                       attempts=row["attempts"] + 1)
            print(f"FAIL ep{row['ep']}p{row['part']}: {str(exc)[:160]}")
        finally:
            con.close()

    print(f"Done: {len(rows) - failures} uploaded, {failures} failed.")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
