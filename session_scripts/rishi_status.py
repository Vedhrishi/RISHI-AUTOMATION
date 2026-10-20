"""RISHI status: one read-only command that answers "is everything scheduled and healthy?"

Usage (from anywhere):  python rishi_status.py [--days 10]
Install: copy next to master_orchestrator.py (R:\\INSTA\\sebi_fin_engine) or run with
PYTHONPATH pointing there. Makes NO writes and posts nothing.

Checks: credentials/tokens, queue coverage per platform through today+N days (real API
IDs vs Business Suite placeholders), YouTube privacy/publishAt via the API, Instagram
live posts + recent reach, Facebook scheduled posts, scheduled-task health, today's log
errors, disk space, website repo + live site freshness.
Exit code: 0 = nothing needs attention, 1 = at least one problem.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE = Path(os.getenv("RISHI_ENGINE", r"R:\INSTA\sebi_fin_engine"))
sys.path.insert(0, str(ENGINE))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE / ".env", override=True)
import social_publishers as sp  # noqa: E402

PLACEHOLDER = "business-suite"
problems: list[str] = []
real_public_count: list[int] = []  # set by youtube(), read by website() - real API truth,
                                    # not the calendar-date guess that caused a false positive


def when(text: str) -> datetime:
    """Parse a scheduled_for value; naive timestamps (older rows) are treated as UTC."""
    d = datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ok(msg): print(f"  OK    {msg}")
def warn(msg): print(f"  WARN  {msg}"); problems.append(msg)
def head(t): print(f"\n== {t} ==")


def section(title, fn):
    head(title)
    try:
        fn()
    except Exception as exc:  # never let one check hide the others
        warn(f"{title}: check failed ({str(exc)[:140]})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10, help="schedule horizon to verify (days ahead)")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=args.days)
    con = sqlite3.connect(os.getenv("DB_PATH", str(ENGINE / "topics_pipeline.db")))
    con.row_factory = sqlite3.Row
    pt = os.getenv("FB_PAGE_ACCESS_TOKEN")
    app_tok = f"{os.getenv('FB_APP_ID')}|{os.getenv('FB_APP_SECRET')}"
    print(f"RISHI STATUS  {now.astimezone(timezone(timedelta(hours=5, minutes=30))):%Y-%m-%d %H:%M} IST   horizon: next {args.days} days")

    def creds():
        for label, tok in (("Page token", pt), ("User token", os.getenv("META_ACCESS_TOKEN"))):
            d = sp._graph_get("debug_token", {"input_token": tok, "access_token": app_tok})["data"]
            exp = d.get("expires_at", 0)
            left = None if not exp else (datetime.fromtimestamp(exp, timezone.utc) - now).days
            if not d.get("is_valid"):
                warn(f"{label} INVALID - generate a new one in Graph API Explorer")
            elif left is not None and left < 14:
                warn(f"{label} expires in {left} days - run meta_token_renew.py")
            else:
                ok(f"{label} valid ({'no fixed expiry' if left is None else f'{left} days left'})")
        c = sp.get_youtube_credentials()
        ok(f"YouTube credentials valid (refresh token: {bool(c.refresh_token)})")
    section("Credentials", creds)

    def coverage():
        rows = con.execute("select * from episodes where scheduled_for is not null order by ep, part").fetchall()
        due = [r for r in rows if when(r["scheduled_for"]) <= horizon]
        print(f"  {len(due)} parts fall due in the window (through {horizon:%Y-%m-%d})")
        for label, col in (("YouTube", "yt_video_id"), ("Facebook", "fb_video_id"), ("Instagram", "ig_media_id")):
            real = [r for r in due if r[col] and r[col] != PLACEHOLDER]
            manual = [r for r in due if r[col] == PLACEHOLDER]
            gaps = [r for r in due if not r[col]]
            line = f"{label:9s} api={len(real)} business-suite={len(manual)} gaps={len(gaps)}"
            if gaps:
                span = f"ep{gaps[0]['ep']}p{gaps[0]['part']} ({gaps[0]['scheduled_for'][:10]}) .. ep{gaps[-1]['ep']}p{gaps[-1]['part']} ({gaps[-1]['scheduled_for'][:10]})"
                warn(f"{line}  -> unscheduled: {span}")
            else:
                ok(line)
        errs = con.execute("select ep,part,substr(coalesce(last_error,''),1,90) e from episodes where status='error' or fb_status='error' or yt_status='error' or ig_status='error'").fetchall()
        for e in errs:
            warn(f"row error ep{e['ep']}p{e['part']}: {e['e']}")
        miss = [r["ep"] for r in due if not Path(r["video_path"]).exists()]
        warn(f"video file missing for ep(s) {sorted(set(miss))}") if miss else ok("all due video files exist on disk")
    section("Queue coverage", coverage)

    def youtube():
        from googleapiclient.discovery import build
        rows = con.execute("select ep,part,yt_video_id from episodes where yt_video_id is not null order by ep,part").fetchall()
        yt = build("youtube", "v3", credentials=sp.get_youtube_credentials(), cache_discovery=False)
        st = {}
        ids = [r["yt_video_id"] for r in rows]
        for i in range(0, len(ids), 50):
            for it in yt.videos().list(part="status,statistics", id=",".join(ids[i:i + 50])).execute()["items"]:
                st[it["id"]] = it
        pub = [v for v in st.values() if v["status"]["privacyStatus"] == "public"]
        real_public_count.append(len(pub))
        sched = [v for v in st.values() if v["status"].get("publishAt")]
        gone = [r for r in rows if r["yt_video_id"] not in st]
        views = sorted(int(v["statistics"].get("viewCount", 0)) for v in pub)
        ok(f"{len(pub)} public, {len(sched)} scheduled (latest publishAt {max((v['status']['publishAt'] for v in sched), default='-')[:16]}), median views {views[len(views)//2] if views else 0}")
        if gone:
            warn(f"{len(gone)} DB video id(s) not found on YouTube: {[(r['ep'], r['part']) for r in gone][:6]}")
        # does YouTube's real publish time match the calendar the other platforms follow?
        cal = {r["yt_video_id"]: r for r in con.execute("select ep,part,yt_video_id,scheduled_for from episodes where yt_video_id is not null")}
        slots, off, caught_up = {}, [], []
        for vid, it in st.items():
            pa = it["status"].get("publishAt")
            if not pa or vid not in cal:
                continue
            slots.setdefault(pa[:16], []).append(f"ep{cal[vid]['ep']}p{cal[vid]['part']}")
            slot = when(cal[vid]["scheduled_for"])
            if slot.strftime("%Y-%m-%dT%H:%M") != pa[:16]:
                tag = f"ep{cal[vid]['ep']}p{cal[vid]['part']}"
                # fix_youtube_schedule.py deliberately reschedules an overdue calendar slot
                # to a daytime catch-up time instead of the (already-passed) canonical one -
                # that is correct by design, not a mismatch to flag.
                (caught_up if slot < now else off).append(tag)
        dup = {k: v for k, v in slots.items() if len(v) > 1}
        warn(f"{len(off)} scheduled YouTube video(s) publish at a different time than the calendar (e.g. {off[:3]}) - see fix_youtube_schedule.py") if off else ok("YouTube publish times match the calendar")
        if caught_up:
            print(f"  NOTE  {len(caught_up)} video(s) at a catch-up slot (calendar date already passed, by design): {caught_up[:5]}")
        warn(f"double-booked YouTube slot(s): {dict(list(dup.items())[:3])}") if dup else ok("no double-booked YouTube slots")
    section("YouTube (API)", youtube)

    def instagram():
        r = sp._graph_get(sp.os.getenv("IG_USER_ID"), {"fields": "username,media_count,followers_count,media.limit(10){timestamp}", "access_token": pt})
        media = r.get("media", {}).get("data", [])
        last = media[0]["timestamp"] if media else None
        ok(f"@{r['username']}: {r['media_count']} live posts, {r['followers_count']} followers, last post {last}")
        reaches = []
        for m in sp._graph_get(sp.os.getenv("IG_USER_ID") + "/media", {"fields": "id", "limit": 10, "access_token": pt}).get("data", []):
            d = sp._graph_get(m["id"] + "/insights", {"metric": "reach", "access_token": pt})["data"]
            reaches.append(d[0]["values"][0]["value"])
        if reaches:
            reaches.sort()
            ok(f"reach on last {len(reaches)} posts: median {reaches[len(reaches)//2]}, max {max(reaches)}")
        print("  NOTE  posts queued in Business Suite are not visible to the API - check Planner for 07:00 + 19:00 IST daily.")
    section("Instagram (API)", instagram)

    def facebook():
        pid = os.getenv("FB_PAGE_ID")
        info = sp._graph_get(pid, {"fields": "name,followers_count", "access_token": pt})
        ok(f"page '{info['name']}', {info.get('followers_count', 0)} followers")
        try:
            sc = sp._graph_get(pid + "/scheduled_posts", {"fields": "id,scheduled_publish_time", "limit": 100, "access_token": pt}).get("data", [])
            times = sorted(int(x["scheduled_publish_time"]) for x in sc if x.get("scheduled_publish_time"))
            ok(f"{len(sc)} scheduled post(s) visible to the API" + (f", latest {datetime.fromtimestamp(times[-1], timezone.utc):%Y-%m-%d %H:%M} UTC" if times else ""))
        except Exception as exc:
            print(f"  NOTE  could not list scheduled posts ({str(exc)[:80]})")
    section("Facebook (API)", facebook)

    def tasks():
        ps = ("foreach($n in 'RISHI_Morning_Drop_Part1','RISHI_Evening_Drop_Part2','RISHI_YT_Topup_Daily'){"
              "$t=Get-ScheduledTask -TaskName $n;$i=Get-ScheduledTaskInfo -TaskName $n;"
              "\"$n|$($t.Principal.LogonType)|$($i.LastRunTime)|$($i.LastTaskResult)|$($i.NextRunTime)\"}")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip().splitlines()
        for line in out:
            n, logon, last, res, nxt = line.split("|")
            msg = f"{n}: logon={logon} last={last} result={res} next={nxt}"
            (ok if res == "0" and logon == "S4U" else warn)(msg)
        logf = ENGINE / "logs" / f"orchestrator-{datetime.now():%Y%m%d}.log"
        if logf.exists():
            errs = [l for l in logf.read_text(encoding="utf-8", errors="replace").splitlines() if " ERROR " in l]
            warn(f"{len(errs)} ERROR line(s) in today's orchestrator log, last: {errs[-1][:110]}") if errs else ok("no ERROR lines in today's orchestrator log")
        free = subprocess.run(["powershell", "-NoProfile", "-Command", "[math]::Round((Get-PSDrive R).Free/1GB,1)"], capture_output=True, text=True).stdout.strip()
        (warn if free and float(free) < 10 else ok)(f"R: free space {free} GB")
    section("Tasks, logs, disk", tasks)

    def website():
        web = Path(r"R:\INSTA\website")
        subprocess.run(["git", "-C", str(web), "fetch", "-q", "origin"], capture_output=True)
        sb = subprocess.run(["git", "-C", str(web), "status", "-sb"], capture_output=True, text=True).stdout.splitlines()[0]
        (warn if ("behind" in sb or "ahead" in sb) else ok)(f"git: {sb}")
        local = json.load(open(web / "data" / "videos.json", encoding="utf-8"))["episodes"]
        live = json.load(urllib.request.urlopen("https://rishi-indian-stocks-explained.vercel.app/data/videos.json?x=%d" % now.timestamp(), timeout=20))["episodes"]
        ll, vl = sum(1 for e in local if e["youtube"]), sum(1 for e in live if e["youtube"])
        public_now = real_public_count[0] if real_public_count else None
        print(f"  episodes local={len(local)} live={len(live)};  YouTube links local={ll} live={vl};  actually public on YouTube now={public_now}")
        if public_now is not None and vl < public_now:
            warn(f"live site has {vl} YouTube links but {public_now} videos are actually public - redeploy needed")
        else:
            ok("live site YouTube links up to date")
    section("Website", website)

    head("SUMMARY")
    if problems:
        print(f"  {len(problems)} item(s) need attention:")
        for p in problems: print("   -", p)
        return 1
    print("  everything checks out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
