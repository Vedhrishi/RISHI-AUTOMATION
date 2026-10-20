"""Realign every row to the canonical calendar: Ep N goes live on 2026-10-12 + (N-1),
Part 1 at 07:00 IST (01:30Z), Part 2 at 19:00 IST (13:30Z).

Also marks what Facebook / Instagram already hold via Business Suite so the
engine never double-posts:
  * Facebook: Ep 1-13 (facebook/schedule/fb_status.json, through 24 Oct 19:00)
  * Instagram: Ep 1-31 and Ep 32 Part 1 (Business Suite, through 12 Nov 07:00)

Usage: python fix_timeline.py [--apply]   (default is a dry run)
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import master_orchestrator as mo  # noqa: E402

START = date(2026, 10, 12)
FB_MANUAL_THROUGH_EP = 13
IG_MANUAL_THROUGH = (32, 1)


def canonical(ep: int, part: int) -> datetime:
    day = START + timedelta(days=ep - 1)
    return datetime.combine(day, time(1, 30) if part == 1 else time(13, 30), tzinfo=timezone.utc)


def main() -> int:
    apply = "--apply" in sys.argv
    con = mo.connect()
    mo.seed_database(con)
    changes = 0
    for row in con.execute("SELECT * FROM episodes ORDER BY ep, part").fetchall():
        ep, part = row["ep"], row["part"]
        want = canonical(ep, part).isoformat(timespec="seconds")
        fields: dict[str, str] = {}
        if row["status"] != "published" and ep >= 6 and row["scheduled_for"] != want:
            fields["scheduled_for"] = want
        if ep <= FB_MANUAL_THROUGH_EP and not row["fb_video_id"]:
            fields.update(fb_video_id="business-suite", fb_status="scheduled_manual")
        if (ep, part) <= IG_MANUAL_THROUGH and not row["ig_media_id"]:
            fields.update(ig_media_id="business-suite", ig_status="manual")
        if fields:
            changes += 1
            if ep <= 20 or "scheduled_for" not in fields:
                print(ep, part, {k: v for k, v in fields.items()})
            if apply:
                mo._record(con, row["id"], **fields)
    print(f"{'APPLIED' if apply else 'DRY RUN'}: {changes} rows changed")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
