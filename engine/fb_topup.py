"""Schedule every remaining Facebook Reel on its canonical date (07:00 / 19:00 IST).

Walks rows in date order and stops at the first date Meta refuses to schedule
(that is the maximum window); re-run daily to extend it. Never publishes live.

Usage: python fb_topup.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import master_orchestrator as mo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()
    mo.setup_logging()
    con = mo.connect()
    now = datetime.now(timezone.utc)
    rows = con.execute(
        """SELECT * FROM episodes WHERE fb_video_id IS NULL AND scheduled_for IS NOT NULL
           ORDER BY scheduled_for LIMIT ?""",
        (args.limit,),
    ).fetchall()
    done = 0
    for row in rows:
        at = datetime.fromisoformat(row["scheduled_for"]).astimezone(timezone.utc)
        if at < now + timedelta(minutes=30):
            print(f"SKIP ep{row['ep']}p{row['part']} {at:%d %b %H:%M}Z already due/past")
            continue
        if args.dry_run:
            print(f"WOULD ep{row['ep']}p{row['part']} -> {at:%Y-%m-%d %H:%M}Z")
            continue
        try:
            res = mo._publish_facebook(row, at)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)[:300]
            print(f"STOP at ep{row['ep']}p{row['part']} ({at:%Y-%m-%d}): {msg}")
            mo._record(con, row["id"], fb_status="error", last_error=msg)
            break
        mo._record(con, row["id"], fb_video_id=res["video_id"], fb_status=res["state"])
        done += 1
        print(f"OK ep{row['ep']}p{row['part']} {at:%Y-%m-%d %H:%M}Z {res['state']} {res['video_id']}", flush=True)
    print(f"Scheduled {done} on Facebook")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
