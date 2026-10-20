"""Run the 14-day batch with Facebook skipped.

The supplied Meta token is valid and permanent but carries only
publish_video/pages_show_list/business_management/instagram_basic, so
POST /{page}/video_reels returns error 200 (pages_read_engagement missing).
Skipping the platform here keeps the batch clean; FB rows stay unscheduled
(fb_video_id NULL) and are picked up by the daily slots once permissions land.

Usage: python sebi_fin_engine/run_batch_youtube_only.py [--days N] [--dry-run]
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import master_orchestrator as mo  # noqa: E402

os.environ["FB_PAGE_ACCESS_TOKEN"] = ""

days = 14
if "--days" in sys.argv:
    days = int(sys.argv[sys.argv.index("--days") + 1])
argv = ["--batch-schedule", "--days", str(days)]
if "--dry-run" in sys.argv:
    argv.append("--dry-run")

raise SystemExit(mo.main(argv))
