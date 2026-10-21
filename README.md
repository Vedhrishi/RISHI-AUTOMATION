# RISHI Automation

> A zero-touch, multi-platform video publishing pipeline for **RISHI | Indian Stocks Explained** — an animated financial-literacy series targeting Indian families, published automatically across YouTube, Facebook, Instagram, and a companion website.

[![Platforms](https://img.shields.io/badge/platforms-YouTube%20%7C%20Facebook%20%7C%20Instagram%20%7C%20Vercel-blue)](https://rishi-indian-stocks-explained.vercel.app)
[![Episodes](https://img.shields.io/badge/episodes-100%20rendered%2C%20200%20parts-orange)](https://rishi-indian-stocks-explained.vercel.app)
[![Languages](https://img.shields.io/badge/languages-EN%20%7C%20HI%20%7C%20TE-green)](https://rishi-indian-stocks-explained.vercel.app)
[![Status](https://img.shields.io/badge/pipeline-live%20%26%20automated-brightgreen)](https://rishi-indian-stocks-explained.vercel.app)

---

## What it does

Every day at 07:00 and 19:00 IST, a Windows Scheduled Task fires without any human involvement:

1. Queries a SQLite database for which episode-parts are due.
2. Calls the YouTube Data API v3 to schedule the video for publication.
3. Calls the Facebook Graph API to upload and schedule the Reel.
4. Calls the Instagram Graph API to create and publish the Reel.
5. If the count of public YouTube videos changed, regenerates the website's `videos.json` and redeploys to Vercel.
6. At three other times during the day, a health-check script (`rishi_status.py`) cross-references six independent data sources and drops a Desktop alert file if anything needs attention.

**Nothing needs to be running, logged in, or touched.** The six scheduled tasks use Windows' S4U logon type, which runs the task as the user account without requiring a stored password.

---

## Architecture

```
topics_pipeline.db  (SQLite, single source of truth)
         │
         ▼
master_orchestrator.py
    ├─ social_publishers.py
    │       ├─ YouTube Data API v3  →  schedule_youtube_video()
    │       ├─ Facebook Graph API   →  schedule_facebook_reel()   ← FB URL bug fixed here
    │       └─ Instagram Graph API  →  publish_instagram_reel()
    │
    └─ Supabase Storage  (video hosting for GH Actions path)

Windows Task Scheduler (6 tasks, S4U logon)
    ├─ RISHI_Morning_Drop_Part1  06:50 IST
    ├─ RISHI_Evening_Drop_Part2  18:50 IST
    ├─ RISHI_YT_Topup_Daily      07:00 IST
    ├─ RISHI_FB_Topup_Daily      07:05 IST
    ├─ RISHI_Alert               07:10 / 13:10 / 19:10 IST
    └─ RISHI_Website_Sync        07:15 / 11:15 / 19:15 IST

rishi_status.py  (read-only health check, cross-references all 6 data sources)
```

**Database schema (key columns):** `ep`, `part`, `title`, `description`, `tags`, `video_path`, `scheduled_for`, `status`, `yt_video_id`, `yt_status`, `fb_video_id`, `fb_status`, `ig_media_id`, `ig_status`, `video_url` (Supabase URL for GH Actions path).

---

## Notable bugs found and fixed in production

### 1. Facebook URL-doubling — silent for months
`_graph_post()` and `_graph_get()` unconditionally prepended the Graph API base URL to the path argument. `schedule_facebook_reel()` passed a fully-formed URL it had already constructed — resulting in `https://graph.facebook.com/v19.0/https://graph.facebook.com/v19.0/...`. Facebook silently treated this as an Open Graph scrape request and returned a plausible JSON response. No actual Reels were ever posted.

**Fix:** one line per helper function:
```python
url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
```
Confirmed end-to-end: 18 real Facebook posts went through in the next run.

### 2. Stat counter race condition (website)
GSAP's scroll-pin continuously mutates the DOM. A `MutationObserver` watching for newly-visible elements re-registered already-visible `[data-count]` targets with the `IntersectionObserver` on every GSAP frame, resetting `countUp()`'s `t₀` continuously. All displayed values stayed near 0.

**Fix:** guard before registering:
```javascript
$$('[data-count]').forEach(b => { if (!b.classList.contains('in')) io.observe(b); });
```
Found by writing a Playwright scroll-churn reproduction script, not code review.

### 3. Three.js coin: orientation + UV smearing
`CylinderGeometry` orients its long axis along Y; the camera was looking along Y → rendered edge-on. Cap UVs are radial, not planar → logo texture smeared as concentric circles.

**Fixes:** `coinGeo.rotateX(Math.PI / 2)` baked into geometry; custom `replanarizeCapUVs()` function recomputing cap UVs from XZ position.

---

## Repository layout

```
├── engine/               # Live posting engine code (no credentials)
│   ├── master_orchestrator.py
│   ├── social_publishers.py   ← URL bug fix is here
│   ├── fb_topup.py
│   ├── fix_timeline.py
│   └── requirements.txt
│
├── session_scripts/      # Tools built across working sessions
│   ├── rishi_status.py        ← full health check, 6 data sources
│   ├── rishi_alert.py         ← surfaces Desktop alert on any problem
│   ├── rishi_cockpit.py       ← manual catch-up for infrequent runs
│   ├── fix_youtube_schedule.py← realigns YouTube publishAt to calendar
│   └── meta_token_renew.py    ← keeps Meta token from ever expiring
│
├── github_actions/       # Scoped GitHub Actions version (not yet live)
│   ├── rishi-publish-v2.yml   ← improved: Meta token writes back to secret
│   ├── ci_runner.py
│   ├── SECRETS_CHECKLIST.md
│   └── set_github_secrets.ps1
│
├── project_data/         # Schedule/caption/metadata pulled from DB
│
├── RETROSPECTIVE.md      ← Engineering write-up: what broke, why, how fixed
├── PROJECT_STATUS.md     ← Full current state, open decisions
└── .gitignore            ← videos/, credentials/, .env excluded
```

**Not in this repo:** `videos/` (5.3 GB), `.env`, credentials — see `.gitignore`. The 190 unpublished video files are already hosted at a Supabase Storage bucket (`rishi-archive`) for the GitHub Actions path.

---

## Tech stack

| Layer | Tech |
|---|---|
| Pipeline language | Python 3.11 |
| State DB | SQLite (`topics_pipeline.db`) |
| Video hosting (cloud path) | Supabase Storage |
| YouTube automation | YouTube Data API v3, `google-auth` OAuth2 |
| Facebook automation | Facebook Graph API v21.0, Reels publishing |
| Instagram automation | Instagram Graph API, Content Publishing |
| Website | Static JSON + Vercel CLI deploy |
| 3D website intro | Three.js r165, GSAP 3.12.5 + ScrollTrigger |
| Testing | Playwright (Python), headless Chromium |
| Scheduling | Windows Task Scheduler, S4U logon type |
| CI/CD (in progress) | GitHub Actions |

---

## What's still open

- **GitHub Actions path:** All code is written (`github_actions/`). Remaining steps: push `sebi_fin_engine` to its own git repo and install secrets (see `SECRETS_CHECKLIST.md`). The Meta token write-back now works in `rishi-publish-v2.yml`.
- **Hindi/Telugu posting:** 200 dubbed files per language exist. Engine needs a `language` column and a minor publisher change.
- **265 remaining episodes:** The Manim render pipeline exists; this is a compute and time constraint.

---

## Running locally

```bash
# Prerequisites: R:\INSTA\sebi_fin_engine\.env with all keys set
cd R:\INSTA\sebi_fin_engine

# Health check (read-only, no writes, no posts)
python rishi_status.py --days 10

# Dry-run: what would be posted in the next run?
python master_orchestrator.py --dry-run

# One-time: realign YouTube publish times with the calendar
python fix_youtube_schedule.py --dry-run
python fix_youtube_schedule.py --apply   # after verifying
```

---

*Live at: [rishi-indian-stocks-explained.vercel.app](https://rishi-indian-stocks-explained.vercel.app)*
*Channel: [@indian.stocks.explained](https://www.youtube.com/@indian.stocks.explained)*
