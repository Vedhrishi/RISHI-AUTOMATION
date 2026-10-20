import sys
sys.path.insert(0, r"R:\INSTA\sebi_fin_engine")
from dotenv import load_dotenv
load_dotenv(r"R:\INSTA\sebi_fin_engine\.env", override=True)
import master_orchestrator as mo
from datetime import datetime

con = mo.connect()
row = con.execute("SELECT * FROM episodes WHERE ep=14 AND part=1").fetchone()

if row["fb_video_id"]:
    print("Already has fb_video_id, nothing to do:", row["fb_video_id"])
else:
    publish_at = datetime.fromisoformat(row["scheduled_for"])
    print("Retrying Facebook Reel for ep14 part1, publish_at =", publish_at)
    try:
        res = mo._publish_facebook(row, publish_at)
        mo._record(con, row["id"], fb_video_id=res["video_id"], fb_status=res["state"])
        row2 = con.execute("SELECT * FROM episodes WHERE id=?", (row["id"],)).fetchone()
        complete = bool(row2["yt_video_id"] and row2["fb_video_id"] and row2["ig_media_id"])
        new_status = "published" if complete else "partial"
        mo._record(con, row["id"], status=new_status, last_error=None)
        print("SUCCESS:", res)
        print("new status:", new_status)
    except Exception as exc:
        mo._record(con, row["id"], fb_status="error", last_error=str(exc)[:900], attempts=row["attempts"] + 1)
        print("STILL FAILING:", str(exc)[:500])
