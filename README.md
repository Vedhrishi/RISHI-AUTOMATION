# RISHI Automation

The automation engine, tooling, and status docs for **RISHI | Indian Stocks
Explained** — an animated financial-literacy series published automatically
across YouTube, Facebook, Instagram, and its own website.

**This repo is code and docs only.** No credentials, no video files (5.3GB,
deliberately left out of git — see below for where they actually live), no
live account access. It's a snapshot/backup/reference, not something that
runs on its own — the actual live pipeline runs from `R:\INSTA\sebi_fin_engine`
and `R:\INSTA\website` on the production machine.

## Layout
- `engine/` — a copy of the live posting engine (`master_orchestrator.py`,
  `social_publishers.py`, `fb_topup.py`, `fix_timeline.py`, `setup_wizard.py`,
  `retry_scheduled_uploads.py`, `run_batch_youtube_only.py`,
  `migrate_videos_to_supabase.py`, `ci_runner.py`, `requirements.txt`). Two of
  these (`fb_topup.py`, `fix_timeline.py`, `setup_wizard.py`) were built by a
  different Claude Code session, not this one — read them before changing
  engine behavior.
- `session_scripts/` — tools built across this project's working sessions:
  `meta_token_renew.py` (keeps the Meta token from expiring),
  `rishi_cockpit.py` (manual catch-up app for infrequent runs),
  `rishi_status.py` (read-only full health/coverage report),
  `rishi_alert.py` (surfaces a Desktop warning file when something needs
  attention), `fix_youtube_schedule.py` (realigns YouTube publish times with
  the canonical calendar).
- `github_actions/` — a scoped-but-not-yet-live GitHub Actions version (so
  Instagram could post on a true daily cadence without the laptop on).
  `SECRETS_CHECKLIST.md` has the exact remaining steps; `set_github_secrets.ps1`
  sets every secret straight from local files without ever displaying a value.
- `project_data/` — schedule/caption/metadata JSON and CSVs pulled from the
  live database at various points, for reference.
- `PROJECT_STATUS.md` — the fullest write-up of what's been built, what's
  verified, what's still open, and why specific decisions were made.
- `EPISODE_TOPICS.csv`, `TITLE_REWRITE_FULL.csv`, `VIDEO_INVENTORY.csv`,
  `FULL_VIDEO_SCAN_REPORT.md`, `IG_POST_INSIGHTS.csv` — working outputs from
  content/quality/performance reviews done over the course of the project.

## Where the actual video/audio files are
Not in this repo (5.3GB across English/Hindi/Telugu is the wrong fit for
git). They're at `C:\Users\vedhr\RISHI_AUTOMATION\videos\` locally, and the
canonical source masters are at `R:\INSTA\output` on the production machine.
All 190 still-unpublished episodes are also already hosted at a public
Supabase Storage bucket (`rishi-archive`) for the GitHub Actions path.

## Status, in one line
YouTube and Facebook post automatically and are verified working end-to-end
(including a real posting-pipeline bug found and fixed this session).
Instagram's posting code is tested and clean but has never run live outside
Business Suite. The website auto-deploys when something new goes public.
Full detail, as of the last update, is in `PROJECT_STATUS.md`.
