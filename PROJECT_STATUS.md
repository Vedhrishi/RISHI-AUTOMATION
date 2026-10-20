# RISHI Automation — Project Status
_Consolidated 2026-10-20 while the owner was AFK. This folder is a CODE + DOCS
consolidation only — no videos were moved or deleted, nothing in R:\INSTA was
touched. See "What was deliberately NOT done" at the bottom._

## What's in this folder
- `engine/` — a copy of the live posting engine's code (from
  `R:\INSTA\sebi_fin_engine`). **No credentials copied** — `.env`,
  `meta_keys.env`, and `credentials/` were deliberately excluded.
  - `master_orchestrator.py` / `social_publishers.py` — the core pipeline
  - `fb_topup.py`, `fix_timeline.py`, `setup_wizard.py` — **built by someone
    else (almost certainly the other Claude Code cloud session working on
    this project), discovered today, not by this session.** Read these
    before changing anything — `fix_timeline.py` encodes which episodes
    were already posted manually via Meta Business Suite
    (Facebook ep≤13, Instagram ep≤32 part1) so the engine doesn't double-post.
- `session_scripts/` — everything *this* session built:
  - `meta_token_renew.py` — re-exchanges the Meta token before its ~60-day
    expiry. Not yet installed into the live engine folder.
  - `retry_ep14.py` — one-off retry for a stale Facebook error on ep14 part1.
  - `rishi_cockpit.py` — the "run every 10-12 days" manual catch-up app.
- `topics_pipeline.SNAPSHOT_*.db` — a read-only copy of the live queue
  database as of the time this folder was created. **The real, live
  database stays in `R:\INSTA\sebi_fin_engine\topics_pipeline.db` — don't
  run anything against this snapshot.**
- `VIDEO_INVENTORY.csv` — all 200 episode-parts (ep, part, status, file
  size, path), generated from the live DB. All 200 files exist on disk;
  total size actively needed by the pipeline: **~1.47 GB**.
- `github_actions/` — the scoped GitHub Actions version (so Instagram can
  post on a true daily cadence with the laptop off): `migrate_videos_to_supabase.py`,
  `ci_runner.py`, `rishi-publish.yml`, `SECRETS_CHECKLIST.md`. All compiled
  clean; none of it is installed or live — see `SECRETS_CHECKLIST.md` for
  the exact install order and the one known gap (Meta token renewal inside
  the workflow doesn't yet write itself back to the GitHub secret).

## Videos + data, consolidated by language (2026-10-20, later same session)
Per request: copied (not moved — nothing deleted from `R:\INSTA`) into
`videos/english/`, `videos/hindi/`, `videos/telugu/`, plus `thumbnails/`
and `project_data/`. Verified counts match source exactly, zero copy errors:

| Folder | Files | Size | Contents |
|---|---|---|---|
| `videos/english/` | 370 | 1.41 GB | 207 `.mp4` + 163 `.srt` (every loose file in `output/`) |
| `videos/hindi/` | 600 | 1.67 GB | 200 `.mp4` + 200 `.m4a` + 200 `.srt` (from `output/dubbed`, `_hi` suffix) |
| `videos/telugu/` | 600 | 1.66 GB | 200 `.mp4` + 200 `.m4a` + 200 `.srt` (from `output/dubbed`, `_te` suffix) |
| `thumbnails/` | 606 | 97 MB | Full copy of `R:\INSTA\thumbnails` |
| `project_data/` | 8 | small | `captions_all.json`, `instagram_captions_ep33_100.json`, `instagram-captions-and-schedule.md`, `schedule_manifest.csv`, `content-calendar-oct-dec-2026.md`, `series_cap.json`, `video_checksums.json`, `qa_report.json` |

**Total: 4.85 GB, all under `C:\Users\vedhr\RISHI_AUTOMATION\`** — on the
real local `C:` disk, not the Google-Drive-synced `R:`, so this copy itself
doesn't trigger any Drive upload/sync traffic.

**Deliberately left out** (nothing here was touched or deleted in
`R:\INSTA` either — just not duplicated into this folder):
- `output/old/` — confirmed junk on inspection: contains a file under a
  different character name (`kabir_ep02...`) and a folder literally named
  `superseded_ep033`.
- `video/` (7.6 GB) — turned out to be render **pipeline source code and
  intermediate build artifacts** (365 per-episode `.py` generator scripts,
  render logs, probe audio, PNG frame mockups), not finished per-language
  video deliverables. Not "videos and data" in the sense asked for.
- `output/covers/` (1.8 MB) — cover images; small, left out only because it
  wasn't clearly "video" or language-specific data — easy to add if wanted.
- `yt_autopilot/` — confirmed unrelated separate project, untouched.

Said differently: every Hindi and Telugu dub that exists (200 of each,
perfectly 1:1 with the 200 English parts) is now here, alongside the
English originals, captions in all three languages, thumbnails, and the
cross-language scheduling/caption metadata — nothing selectively dropped
within those three languages.

## Added 2026-10-21 (autonomous improvement pass)
- `RISHI_ROUTINE.ps1` — ONE guided command for every manual step (status → Facebook
  top-up → YouTube realign → website rebuild/deploy → status). Dry run first, y/N before
  anything live. Run: `powershell -ExecutionPolicy Bypass -File C:\Users\vedhr\RISHI_AUTOMATION\RISHI_ROUTINE.ps1`
- `session_scripts/rishi_status.py` — read-only health/coverage report (tokens, per-platform
  coverage vs the calendar, YouTube API truth, Instagram reach, Facebook, tasks, logs, disk,
  website freshness). Exit code 1 if anything needs attention.
- `session_scripts/fix_youtube_schedule.py` — **real bug found**: ep8–15 were uploaded to
  YouTube before the calendar was realigned, so they publish 2 days later than IG/FB and
  ep14/15 collide with ep16/17 in the same slot (27–28 Oct). Dry-run default; `--apply`
  moves them to canonical times; overdue ones go to daytime slots (12:00/16:00 IST).
- `website/tools/build_data.py` patched (uncommitted, in `R:\INSTA\website`): now links
  engine-uploaded YouTube videos that are PUBLIC (credential-free oEmbed check), so the
  site no longer depends on a hand-edited `yt_status.json`. Tested against a stale file.
- Instagram analysis (`IG_POST_INSIGHTS.csv`): 18 live posts, median reach 143.5. Part 1
  (07:00) median 152 / mean 299 vs Part 2 (19:00) median 133 / mean 139; small sample.
  Only 1 of 137 unposted captions opened with a "PART 2:" label (ep32p2, fixed).
- Vercel is a CLI deploy, not GitHub-triggered (so a git push alone never updates the site).
- Suggested next automation (needs you): register `fb_topup.py` as a daily task so Facebook
  stays scheduled ahead on its own (its header says "re-run daily to extend").

## Install steps still needed (none done yet — all need you present)
1. Copy `session_scripts/*.py` into `R:\INSTA\sebi_fin_engine\`.
2. Register `meta_token_renew.py` as a biweekly scheduled task (command was
   given in chat).
3. Run `retry_ep14.py` once to confirm the Facebook fix worked.
4. Run `rishi_cockpit.py --dry-run` then live, every 10-12 days going forward.

## R:\INSTA — what's there and why nothing was moved/deleted
`R:\INSTA` is a **Google Drive–synced folder** (`GoogleDriveFS.exe` was
found running; the drive carries `.tmp.drivedownload` / `.tmp.driveupload`
sync-in-progress markers). A bulk move or delete there doesn't just touch
local disk — it can cascade into real, large uploads/downloads against the
actual Google Drive account, and deletions sync too. Combined with the
Windows Scheduled Tasks pointing at fixed absolute paths under
`R:\INSTA\sebi_fin_engine`, and the folder holding what looks like the only
copy of rendered production video, this was judged too risky to execute
unsupervised. Full breakdown, sizes in `R:\INSTA`:

| Folder | Size | Referenced by the live DB? | Notes |
|---|---|---|---|
| `output/` (loose files only) | ~1.5 GB | **Yes — all 200 active files** | The actual pipeline input |
| `output/dubbed/` | 3.6 GB | No | Likely alternate-language dubs; not wired into any DB row |
| `video/` (`build`,`episodes`,`v2`,`v3`) | 7.6 GB | No | Looks like raw/intermediate master renders — possibly irreplaceable source material |
| `output/old/` | 135 MB | No | Name suggests superseded/archival |
| `yt_autopilot/` | 442 MB | No | **A different, unrelated project** (scrape→script→voice→upload tool, dated 11 Oct, separate `venv/`) — not part of the RISHI pipeline at all |
| `website/` | 137 MB | n/a | Separate git clone of the GitHub repo — see the gap noted below |
| `thumbnails/`, `youtube/`, `facebook/`, `marketing/` | ~150 MB combined | Partially | Captions/thumbnails/schedules — real project content, not automation code |
| Top-level loose `.md`/`.json` files | small | No | Episode scripts, season notes, QA/checksum reports — real creative content, not automation |
| `.claude/`, `.freebuff/`, `.tmp.drivedownload/`, `.tmp.driveupload/` | — | No | Editor config / Drive sync internals — safe to ignore, not project data |

**My recommendation, for you to execute yourself**: `output/dubbed`,
`output/old`, and `video/` are the only genuinely large, judgment-call
items. If you're confident `video/` is superseded by the final renders in
`output/`, that's 11+ GB recoverable — but confirm it holds no master files
you'd ever need again before deleting, since Google Drive's trash recovery
window is time-limited. `yt_autopilot/` is a separate project; move it out
of this folder entirely rather than deleting, since it's unrelated to RISHI.

## Website gap — re-investigated, now a known-safe fix (not pushed yet)
Dug into exactly what the 3 remote commits are and whether they'd conflict
with the uncommitted local regeneration. They're from the other Claude Code
session (`session_01JqKZ6Y8QgjLzTf2LaWjv78` — the one that messaged this
session at the very start): two commits adding a `publisher/` desktop app
(Tkinter), one commit backfilling real YouTube links for ep6/ep7 part1 into
`data/videos.json` + `supabase/seed.sql`.

`data/videos.json` is stored as one giant single-line minified JSON blob, so
any two independent regenerations look like a full-file git conflict even
when the actual data mostly agrees — checked this directly: **the remote's
backfilled video IDs for ep6 p1/p2 and ep7 p1 are byte-identical to what's
already in the live DB**, and the live DB additionally already has ep7
part2's link, which the remote's version is still missing (empty string).

**Conclusion: regenerating `data/videos.json` + `supabase/seed.sql` fresh
from the current `topics_pipeline.db` (via `build_data.py`) is a strict
superset of both the remote commit and the stale local copy — no data is
lost either way.** The real fix is: `git pull` the 3 remote commits (no
actual conflict with anything else they touched), run `build_data.py`
fresh, commit, push. Not done yet — pushing to this shared, actively-worked
repo is an outward-facing action I'm holding for an explicit go-ahead
rather than doing unprompted while unsupervised.
- **GitHub Actions plan** (so Instagram posts daily without the laptop on)
  was scoped in chat; build artifacts for it are in `session_scripts/` or
  noted in chat, pending your review before any secrets are set.

## What was deliberately NOT done while you were away
- Nothing in `R:\INSTA` was moved, renamed, or deleted.
- No credentials were copied anywhere outside their existing `.env`.
- No live posts, scheduled-task registrations, or GitHub pushes were made —
  the sandbox blocks all of these for the agent regardless of prior
  go-ahead; they need you to run them directly (exact commands were given
  in chat each time).
