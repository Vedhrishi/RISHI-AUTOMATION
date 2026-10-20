"""Keep the Meta (Facebook/Instagram) long-lived user token from ever expiring.

A long-lived user token lasts ~60 days. Re-exchanging it via
`fb_exchange_token` *before* it expires issues a fresh 60-day token, which
means this never needs a human to log back into Graph API Explorer as long
as it's run at least once somewhere inside every 60-day window.

Run modes
---------
  python meta_token_renew.py --check   Read-only: report current token
                                       validity/expiry, no API writes.
  python meta_token_renew.py           Re-exchange + refresh Page/IG ids,
                                       persist to .env (what the scheduled
                                       task actually runs).

Install: copy this file into R:\INSTA\sebi_fin_engine\meta_token_renew.py
so it sits next to .env and social_publishers.py.

Logs to logs/meta_renew-YYYYMMDD.log alongside the orchestrator's own logs.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE / ".env", override=True)

import social_publishers as sp  # noqa: E402

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
log = logging.getLogger("meta_renew")
log.setLevel(logging.INFO)
_handler = logging.FileHandler(
    LOG_DIR / f"meta_renew-{datetime.now():%Y%m%d}.log", encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
log.addHandler(_handler)
log.addHandler(logging.StreamHandler(sys.stdout))

# Renew proactively once inside this many days of expiry, not only at the deadline.
RENEW_WINDOW_DAYS = 20


def check_token() -> dict:
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        return {"valid": False, "error": "META_ACCESS_TOKEN is not set"}
    try:
        d = sp._graph_get(
            "debug_token",
            {
                "input_token": token,
                "access_token": f"{os.getenv('FB_APP_ID')}|{os.getenv('FB_APP_SECRET')}",
            },
        )["data"]
    except Exception as exc:
        return {"valid": False, "error": str(exc)[:300]}
    expires_at = d.get("expires_at", 0)
    days_left = (
        None
        if not expires_at
        else (datetime.fromtimestamp(expires_at, tz=timezone.utc) - datetime.now(timezone.utc)).days
    )
    return {"valid": d.get("is_valid"), "expires_at": expires_at, "days_left": days_left}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only status, no renewal")
    ap.add_argument("--force", action="store_true", help="renew even if not near expiry")
    args = ap.parse_args()

    status = check_token()
    log.info("Current token: %s", status)

    if args.check:
        return 0 if status.get("valid") else 1

    if not status.get("valid"):
        log.error("Token is invalid; cannot self-renew. A human must generate a new one "
                   "in Graph API Explorer and paste it into META_ACCESS_TOKEN.")
        return 2

    days_left = status.get("days_left")
    if not args.force and days_left is not None and days_left > RENEW_WINDOW_DAYS:
        log.info("Token has %s days left (> %s-day window); skipping renewal.",
                  days_left, RENEW_WINDOW_DAYS)
        return 0

    try:
        info = sp.setup_meta_credentials()
        log.info("Renewed OK: page_id=%s ig_user_id=%s long_lived=%s",
                  info["page_id"], info["ig_user_id"], info["long_lived"])
        new_status = check_token()
        log.info("New token: %s", new_status)
        return 0
    except Exception as exc:
        log.exception("Renewal FAILED: %s", exc)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("RISHI_NONINTERACTIVE", "1")
    sys.exit(main())
