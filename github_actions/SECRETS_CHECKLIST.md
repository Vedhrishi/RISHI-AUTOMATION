# GitHub Actions secrets needed (names only — fill values yourself, never paste them to me)

Set at: repo → Settings → Secrets and variables → Actions → New repository secret.
Or via CLI: `gh secret set NAME --repo Vedhrishi/rishi-indian-stocks-explained`
(it'll prompt for the value, or pipe it in — never type secret values in a chat).

| Secret name | Where the value comes from |
|---|---|
| `META_ACCESS_TOKEN` | Current value of this key in `R:\INSTA\sebi_fin_engine\.env` |
| `FB_APP_ID` | Same |
| `FB_APP_SECRET` | Same |
| `FB_PAGE_ID` | Same |
| `FB_PAGE_ACCESS_TOKEN` | Same |
| `IG_USER_ID` | Same |
| `SUPABASE_URL` | Same |
| `SUPABASE_SERVICE_KEY` | Same |
| `YT_TOKEN_JSON` | Full contents of `R:\INSTA\sebi_fin_engine\credentials\yt_token.json` |
| `YT_CLIENT_SECRET_JSON` | Full contents of `R:\INSTA\sebi_fin_engine\credentials\client_secret.json` |

## Before any of this goes live
1. Run `migrate_videos_to_supabase.py --dry-run`, review, then `--apply`.
2. Install `ci_runner.py` and `migrate_videos_to_supabase.py` next to
   `master_orchestrator.py` in the repo (`sebi_fin_engine/`), commit, push.
3. Commit `topics_pipeline.db` itself to the repo (it's just schedule/caption
   metadata, no secrets — safe to version-control).
4. Add a `sebi_fin_engine/requirements.txt` if one doesn't already exist
   (google-api-python-client, google-auth, requests, python-dotenv, supabase).
5. Set every secret above.
6. Copy `rishi-publish.yml` to `.github/workflows/rishi-publish.yml`, push.
7. Trigger it once manually (Actions tab → "Run workflow") before trusting
   the schedule — watch the log for the first real run.
8. Once confirmed working: disable the 3 Windows scheduled tasks so the
   laptop and GitHub Actions never both try to post the same row.
