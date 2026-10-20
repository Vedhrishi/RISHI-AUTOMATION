"""Realign YouTube publish times with the series calendar (the DB's scheduled_for).

Why: ep8-15 were uploaded before the calendar was realigned, so YouTube publishes them two
days later than Instagram/Facebook, and ep14/ep15 collide with ep16/ep17 in the same slot.

Only touches videos that are still PRIVATE with a future publishAt (never public ones).
Videos whose canonical time has already passed go into extra DAYTIME slots between the regular
ones (12:00 and 16:00 IST = 06:30 and 10:30 UTC), so they reach the audience awake and never
collide with the 07:00 / 19:00 IST premieres.

  python fix_youtube_schedule.py            DRY RUN: prints the plan, changes nothing
  python fix_youtube_schedule.py --apply    performs the updates (YouTube API, ~50 quota units each)

Install: copy next to master_orchestrator.py (R:\\INSTA\\sebi_fin_engine) or set RISHI_ENGINE.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE = Path(os.getenv("RISHI_ENGINE", r"R:\INSTA\sebi_fin_engine"))
sys.path.insert(0, str(ENGINE))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE / ".env", override=True)
import social_publishers as sp  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

READ_ONLY_STATUS = {"uploadStatus", "failureReason", "rejectionReason", "madeForKids"}


def when(text: str) -> datetime:
    d = datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


CATCHUP_SLOTS_UTC = ((6, 30), (10, 30))  # 12:00 and 16:00 IST


def catchup_slot(earliest: datetime, n: int) -> datetime:
    """The n-th (0-based) daytime catch-up slot at or after `earliest`."""
    day = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
    found = -1
    while True:
        for hh, mm in CATCHUP_SLOTS_UTC:
            cand = day.replace(hour=hh, minute=mm)
            if cand >= earliest:
                found += 1
                if found == n:
                    return cand
        day += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--lead-minutes", type=int, default=20, help="earliest allowed publish time from now")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    con = sqlite3.connect(os.getenv("DB_PATH", str(ENGINE / "topics_pipeline.db")))
    rows = con.execute(
        "select ep,part,yt_video_id,scheduled_for from episodes "
        "where yt_video_id is not null and yt_video_id != 'business-suite' order by ep,part"
    ).fetchall()
    yt = build("youtube", "v3", credentials=sp.get_youtube_credentials(), cache_discovery=False)
    items = {}
    ids = [r[2] for r in rows]
    for i in range(0, len(ids), 50):
        for it in yt.videos().list(part="status", id=",".join(ids[i:i + 50])).execute()["items"]:
            items[it["id"]] = it

    earliest = now + timedelta(minutes=args.lead_minutes)
    overdue_n = 0
    plan = []
    for ep, part, vid, sf in rows:
        it = items.get(vid)
        if not it or it["status"]["privacyStatus"] != "private" or not it["status"].get("publishAt"):
            continue  # public or unscheduled: never touch
        current = datetime.strptime(it["status"]["publishAt"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        target = when(sf)
        if target < earliest:  # canonical slot already passed -> catch up, staggered
            target = catchup_slot(earliest, overdue_n)
            overdue_n += 1
            note = "catch-up (canonical slot already passed)"
        else:
            note = ""
        if abs((target - current).total_seconds()) < 60:
            continue
        plan.append((ep, part, vid, current, target, note, it))

    print(f"{'DRY RUN' if not args.apply else 'APPLYING'}: {len(plan)} video(s) to reschedule\n")
    print(f"{'video':8s} {'now on YouTube (UTC)':22s} {'new publish time (UTC)':22s} note")
    for ep, part, vid, cur, tgt, note, _ in plan:
        print(f"ep{ep}p{part:<4d} {cur:%Y-%m-%d %H:%M}        {tgt:%Y-%m-%d %H:%M}        {note}")
    if not args.apply:
        print("\nNothing changed. Re-run with --apply to perform these updates.")
        return 0

    done = 0
    for ep, part, vid, cur, tgt, note, it in plan:
        status = {k: v for k, v in it["status"].items() if k not in READ_ONLY_STATUS}
        status["privacyStatus"] = "private"
        status["publishAt"] = iso(tgt)
        yt.videos().update(part="status", body={"id": vid, "status": status}).execute()
        done += 1
        print(f"  updated ep{ep}p{part} -> {tgt:%Y-%m-%d %H:%M} UTC")
    print(f"\nDone: {done}/{len(plan)} updated. Re-run rishi_status.py to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
