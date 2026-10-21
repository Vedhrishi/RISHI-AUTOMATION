# RISHI Automation — Engineering Retrospective

_A candid write-up of what was actually hard, what broke in production, and what I learned building a zero-touch video publishing pipeline for an animated financial-literacy YouTube channel._

---

## What this is

**RISHI | Indian Stocks Explained** is a 365-episode animated series that explains Indian stock-market concepts to everyday families through four recurring characters. Every episode is posted automatically across YouTube, Facebook, and Instagram twice daily, with a companion website that redeploys itself when new videos go public. This is the story of building that automation end-to-end, including the bugs that only showed up in production.

**Stack:** Python, SQLite, YouTube Data API v3, Facebook/Instagram Graph API, Supabase Storage, Vercel, Windows Task Scheduler, Three.js, GSAP, Playwright.

---

## The system, in one paragraph

A SQLite database (`topics_pipeline.db`) is the single source of truth: every episode-part has a row with its scheduled publish date, YouTube video ID, Facebook video ID, Instagram media ID, and status per platform. A Windows Scheduled Task fires twice daily, runs `master_orchestrator.py`, which queries the DB for anything due in the next 10 minutes, and calls the appropriate Graph API or YouTube API to either upload or schedule the post. A second task fires daily to top up Facebook's 28-post scheduling window. A third task redeploys the website when the YouTube video count changes. `rishi_status.py` is a read-only health-check script that runs all six API checks and exits non-zero if anything needs attention.

---

## The bugs that actually mattered

### 1. The Facebook URL-doubling bug (silent, production, months)

**What happened:** Facebook Graph API posts had been returning what looked like success responses — video IDs, upload URLs, the works — but zero actual Reels ever appeared on the page. The bug had been silently present since the pipeline first posted to Facebook.

**Root cause:** `_graph_post()` and `_graph_get()` unconditionally prepended the Graph API base URL (`https://graph.facebook.com/v19.0/`) to every path argument. `schedule_facebook_reel()` passed an already-fully-formed URL (it had constructed `f"{GRAPH}/{page_id}/video_reels"` itself before calling). The result was a doubled URL: `https://graph.facebook.com/v19.0/https://graph.facebook.com/v19.0/...`. Facebook silently interpreted this as an Open Graph scrape request rather than a publishing API call, returned a plausible-looking JSON response, and nothing was actually posted.

**Fix:**
```python
def _graph_post(path: str, data: dict) -> dict:
    url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
    ...
```
One line, both functions. Verified by calling the exact previously-broken code path directly and confirming a real `video_id` and `upload_url` came back. After applying: 18 real Facebook posts went through in the next run.

**Lesson:** APIs that silently succeed on malformed input are the worst category of bug to diagnose. The correct test is not "did it return 200" — it's "does the thing I expected now exist in the real world."

---

### 2. YouTube publish-time drift (double-booking, calendar mismatch)

**What happened:** After catching up on 8 episodes that had been uploaded but not published (ep8–15), `fix_youtube_schedule.py` was needed to realign their `publishAt` times with the master calendar. Some overdue slots had already passed; the script needed to assign daytime catch-up times (06:30/10:30 UTC) instead of the original (already-past) canonical times.

**The subtle correctness problem:** `rishi_status.py`'s schedule-mismatch checker would then flag these as "wrong" — because their `publishAt` didn't match the DB's `scheduled_for` column. But that mismatch was *by design*. The fix was to distinguish between two classes of mismatch:

```python
slots, off, caught_up = {}, [], []
...
(caught_up if slot < now else off).append(tag)
```

Slots whose calendar date has already passed go into `caught_up` (printed as a NOTE, not a WARN); only genuinely wrong future slots go into `off` (which fires an alert).

---

### 3. The website false-positive (wrong data source)

**What happened:** `rishi_status.py` reported "live site has 14 YouTube links but 18 videos are public — redeploy needed" — but the site was actually correct. Investigating revealed the check was comparing the live site's YouTube link count against a calendar-date SQL query (`scheduled_for <= now`) rather than the real YouTube API's privacy status. The two disagreed because the catch-up-slot videos were now public but their DB `scheduled_for` was still in the future relative to the script's logic.

**Fix:** The `youtube()` function already had the real answer. Added a shared module-level list:

```python
real_public_count: list[int] = []
```

`youtube()` appends to it; `website()` reads from it. The status check now compares against YouTube API truth, not a date-based guess. One false positive → zero, permanently.

---

### 4. The stat counter race condition (frontend, production)

**What happened:** The website's animated stat counters (episodes/languages/seasons/seconds) were stuck at "0" on the live site. The counters animated correctly in isolated testing but not after a real scroll through the page.

**Root cause:** A `MutationObserver` watched the DOM for newly-visible elements and registered them with an `IntersectionObserver`. GSAP's scroll-pinning section continuously mutates the DOM (transform updates on every scroll tick). Each mutation re-ran `$$('[data-count]').forEach(b => io.observe(b))`, which re-observed already-visible stat elements — resetting `countUp()`'s `t0 = performance.now()` on every GSAP frame, keeping the displayed value perpetually near 0.

**Fix:**
```javascript
$$('[data-count]').forEach(b => { if (!b.classList.contains('in')) io.observe(b); });
```
One guard. Only un-started counters get registered; once `in` is added, they're ignored by all future mutation events.

**How I caught it:** Not code review — I wrote a Playwright reproduction script that scrolled repeatedly through the GSAP pin section, then read the DOM values. Without the scroll churn, the bug didn't appear.

---

### 5. The Three.js coin orientation + UV mapping (website, visual)

**What happened:** The 3D coin in the cinematic intro section rendered edge-on (a thin line) instead of face-on. After fixing orientation, the logo texture on the coin face was smeared in a radial pattern — unreadable.

**Root causes:**
1. `CylinderGeometry` defaults to Y-axis as the cylinder's long axis. The viewport was looking along Y. Fix: `coinGeo.rotateX(Math.PI / 2)` baked into the geometry before mesh creation.
2. `CylinderGeometry`'s cap UVs are radial (center = (0.5, 0.5), edges = radial offsets), not planar. Any logo texture applied to the cap comes out as concentric circles, not a flat image. Fix: custom `replanarizeCapUVs()` function that iterates the cap's index buffer and rewrites UV coordinates from `(x / (2*r) + 0.5, y / (2*r) + 0.5)`.

Both found by taking an actual screenshot with Playwright, not by reading the code.

---

## What "scheduled tasks that run while you're asleep" actually means

The Windows Task Scheduler tasks use **S4U logon type** — they run under the user account without requiring the password to be stored, and they fire even when the user is logged out (not just locked). This was non-obvious: the default "run whether user is logged on or not" option stores a password hash; S4U avoids this entirely. Six tasks are registered:

| Task | Schedule | Purpose |
|---|---|---|
| `RISHI_Morning_Drop_Part1` | 06:50 IST | Post YouTube + Facebook + Instagram for morning slot |
| `RISHI_Evening_Drop_Part2` | 18:50 IST | Post YouTube + Facebook + Instagram for evening slot |
| `RISHI_YT_Topup_Daily` | 07:00 IST | Ensure 10 episodes are pre-scheduled on YouTube |
| `RISHI_FB_Topup_Daily` | 07:05 IST | Keep Facebook's 28-post buffer full |
| `RISHI_Alert` | 07:10/13:10/19:10 IST | Drop a Desktop file if anything needs attention |
| `RISHI_Website_Sync` | 07:15/11:15/19:15 IST | Redeploy Vercel if YouTube public count changed |

The alert task was the last piece of the reliability puzzle: it means any problem surfaces as a visible file on the Desktop, not as a silent failure days later.

---

## Scale and content

- **100 episodes rendered, 200 episode-parts** (each episode is 2 parts, each ~4–5 minutes)
- **3 languages** (English, Hindi dubbed, Telugu dubbed) — 5.3 GB of finished video
- **All 190 unpublished videos** uploaded to Supabase Storage for the GitHub Actions path
- **Posting cadence:** 07:00 + 19:00 IST, every day, automatically
- **Platforms covered:** YouTube (scheduled privacy), Facebook Graph API (Reels), Instagram Graph API (Reels), Vercel-hosted website

---

## What's genuinely novel (vs. just following the API docs)

1. **The FB bug diagnosis** — required tracing a false-positive API response backwards through a URL-construction bug that had been silently present for months. Isolated the exact failing code path, reproduced it with a raw API call, confirmed the fix end-to-end.

2. **The status script design** — `rishi_status.py` pulls from six independent data sources (two Meta API token debugs, YouTube API, Instagram API, Facebook scheduled posts API, Windows Task Scheduler, Vercel live site) and cross-references them. The `real_public_count` fix shows real API truth beating calendar-date guesses.

3. **Playwright as a production testing tool** — used it to find four real bugs that code review missed: coin edge-on rendering, UV smearing, WebGL GPU stalls (led to a lighting simplification that matters on real low-end devices), and the stat counter race condition. The test approach was: write the reproduction, observe the failure, fix the code, re-run the test, confirm the fix.

4. **Catch-up slot logic** — when a scheduled publish date has passed, the pipeline reschedules to a daytime catch-up slot instead of silently failing or posting at 3am. The `rishi_status.py` mismatch checker understands the difference between "wrong" and "intentionally different because the canonical date passed."

---

## What's still open

- **GitHub Actions path:** All code is written and tested; the remaining step is pushing `sebi_fin_engine` to its own git repo and installing secrets. This is a deliberate "when ready" decision — local automation is proven reliable, and switching to GH Actions is a one-way architectural decision.
- **Hindi/Telugu posting:** All 600 dubbed files exist (200 × 3 languages). The posting engine handles English only. Extending it is a schema migration (add language column) + minor publisher change.
- **Instagram Live automation:** Currently uses Business Suite manual scheduling for IG posts. The API code (`publish_instagram_reel()`) is tested and clean; the remaining step is live authorization and a one-time "first IG post via API" run to verify.
- **Remaining 265 episodes:** 100 of 365 are rendered. The rendering pipeline (Python + Manim) exists; it's a compute and time constraint, not a code constraint.

---

_Last updated: 2026-10-21_
