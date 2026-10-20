"""RISHI social publishers: YouTube Shorts, Facebook Reels, Instagram Reels.

All credentials are read from sebi_fin_engine/.env (python-dotenv).
Nothing in this module prints or returns secrets.

Platform notes
--------------
YouTube : Data API v3, resumable upload, privacyStatus=private + publishAt.
Facebook: Graph API v21.0 video_reels 3-phase upload (start / transfer / finish).
Instagram: Reels container fed by a signed Supabase Storage URL, then published.
"""
from __future__ import annotations

import getpass
import logging
import mimetypes
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=True)

log = logging.getLogger("rishi.publishers")

YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]
GRAPH_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
HTTP_TIMEOUT = 60
YOUTUBE_CHUNK = 1024 * 1024 * 4  # 4 MB resumable chunks (multiple of 256 KB)
FB_SCHEDULE_MIN_LEAD = timedelta(minutes=12)


# ---------------------------------------------------------------------------
# env helpers
# ---------------------------------------------------------------------------
def _resolve(value: str | None, default: Path) -> Path:
    """Resolve an env path, treating relative paths as relative to the engine."""
    if not value:
        return default
    p = Path(value)
    return p if p.is_absolute() else (BASE_DIR / p)


def token_path() -> Path:
    return _resolve(os.getenv("YT_TOKEN_FILE"), BASE_DIR / "credentials" / "yt_token.json")


def client_secret_path() -> Path:
    return _resolve(
        os.getenv("YT_CLIENT_SECRET_FILE"), BASE_DIR / "credentials" / "client_secret.json"
    )


def update_env_file(updates: dict[str, str], path: Path = ENV_FILE) -> None:
    """Persist key=value pairs into .env, preserving comments and ordering."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{k}={updates[k]}" for k in missing)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = v


def credentials_status() -> dict[str, bool]:
    """Presence check used by the orchestrator report (never exposes values)."""
    return {
        "youtube_token": token_path().exists(),
        "youtube_client_secret": client_secret_path().exists(),
        "meta_user_token": bool(os.getenv("META_ACCESS_TOKEN")),
        "fb_page_token": bool(os.getenv("FB_PAGE_ACCESS_TOKEN")),
        "fb_page_id": bool(os.getenv("FB_PAGE_ID")),
        "ig_user_id": bool(os.getenv("IG_USER_ID")),
        "supabase_url": bool(os.getenv("SUPABASE_URL")),
        "supabase_service_key": bool(os.getenv("SUPABASE_SERVICE_KEY")),
    }


# ---------------------------------------------------------------------------
# 1. YouTube
# ---------------------------------------------------------------------------
def get_youtube_credentials(scopes: Iterable[str] = YT_SCOPES) -> Credentials:
    """Load yt_token.json and refresh it if expired; persist the refreshed token."""
    path = token_path()
    if not path.exists():
        raise FileNotFoundError(
            f"YouTube token not found at {path}. Run: "
            "python sebi_fin_engine/master_orchestrator.py --auth-youtube"
        )
    creds = Credentials.from_authorized_user_file(str(path), list(scopes))
    if creds.expired and creds.refresh_token:
        log.info("[YouTube] Access token expired; refreshing")
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
        log.info("[YouTube] Refreshed token written back to %s", path)
    if not creds.valid:
        raise RuntimeError("YouTube credentials are invalid and could not be refreshed.")
    return creds


def run_youtube_oauth_flow() -> Path:
    """One-time local OAuth consent; writes yt_token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = client_secret_path()
    if not secret.exists():
        raise FileNotFoundError(f"Missing OAuth client file: {secret}")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), YT_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    out = token_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    return out


def schedule_youtube_video(
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str],
    publish_at_utc: datetime,
    token_path_override: str | Path | None = None,
    category_id: str | None = None,
    privacy_status: str = "private",
) -> str:
    """Upload an MP4 and schedule it. Returns the YouTube video id.

    publish_at_utc must be timezone-aware; it is normalised to UTC and sent as
    ISO-8601 (YYYY-MM-DDTHH:MM:SSZ).
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if publish_at_utc.tzinfo is None:
        raise ValueError("publish_at_utc must be timezone-aware")

    creds = get_youtube_credentials()
    if token_path_override:
        override = Path(token_path_override)
        if override.exists():
            creds = Credentials.from_authorized_user_file(str(override), YT_SCOPES)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    publish_utc = publish_at_utc.astimezone(timezone.utc)
    iso_publish_time = publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:30],
            "categoryId": category_id or os.getenv("YOUTUBE_CATEGORY_ID", "27"),
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy_status,  # private is required with publishAt
            "publishAt": iso_publish_time,
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=YOUTUBE_CHUNK,
        resumable=True,
        mimetype="video/mp4",
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response: dict[str, Any] | None = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log.info("[YouTube] Upload progress: %d%%", int(status.progress() * 100))
            retries = 0
        except HttpError as exc:
            code = getattr(exc.resp, "status", 0)
            if code in (500, 502, 503, 504) and retries < 5:
                retries += 1
                wait = 2**retries
                log.warning("[YouTube] %s from API; retrying chunk in %ss", code, wait)
                time.sleep(wait)
                continue
            raise
        except (OSError, requests.RequestException) as exc:
            if retries < 5:
                retries += 1
                wait = 2**retries
                log.warning("[YouTube] Network error (%s); retry %s in %ss", exc, retries, wait)
                time.sleep(wait)
                continue
            raise

    video_id = response["id"]
    log.info("[YouTube] Scheduled video %s for %s UTC", video_id, iso_publish_time)
    return video_id


def verify_youtube_token() -> dict[str, Any]:
    """Refresh if needed and confirm the token can read the channel."""
    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = youtube.channels().list(part="id,snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("Token is valid but no YouTube channel is attached to it.")
    channel = items[0]
    return {
        "channel_id": channel["id"],
        "channel_title": channel["snippet"]["title"],
        "expires": creds.expiry.isoformat() if creds.expiry else None,
        "refreshed": bool(creds.refresh_token),
    }


# ---------------------------------------------------------------------------
# 2. Meta / Facebook helpers
# ---------------------------------------------------------------------------
class GraphError(RuntimeError):
    def __init__(self, message: str, payload: Any = None):
        super().__init__(message)
        self.payload = payload


def _graph_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    payload = _json_or_text(resp)
    if resp.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
        raise GraphError(f"Graph GET {path} failed: {payload}", payload)
    return payload


def _graph_post(path: str, data: dict[str, Any]) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
    resp = requests.post(url, data=data, timeout=HTTP_TIMEOUT)
    payload = _json_or_text(resp)
    if resp.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
        raise GraphError(f"Graph POST {path} failed: {payload}", payload)
    return payload


def _json_or_text(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"status_code": resp.status_code, "text": resp.text[:1000]}


def test_meta_token(access_token: str | None = None) -> dict[str, Any]:
    """Validate a Meta token against v21.0 and list its pages."""
    token = access_token or os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        raise GraphError("META_ACCESS_TOKEN is not set.")
    me = _graph_get("me", {"fields": "id,name", "access_token": token})
    accounts = _graph_get(
        "me/accounts",
        {
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "limit": 100,
            "access_token": token,
        },
    )
    return {"me": me, "pages": accounts.get("data", [])}


def exchange_long_lived_user_token(
    short_token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    """Exchange a short-lived user token for a ~60-day long-lived user token."""
    token = short_token or os.getenv("META_ACCESS_TOKEN", "")
    app_id = app_id or os.getenv("FB_APP_ID", "")
    app_secret = app_secret or os.getenv("FB_APP_SECRET", "")
    if not (token and app_id and app_secret):
        raise GraphError("Token exchange needs META_ACCESS_TOKEN, FB_APP_ID and FB_APP_SECRET.")
    payload = _graph_get(
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        },
    )
    return payload


def get_page_access_token(user_token: str, page_id: str) -> str:
    """Return the permanent Page Access Token for page_id."""
    accounts = _graph_get(
        "me/accounts",
        {"fields": "id,name,access_token", "limit": 100, "access_token": user_token},
    )
    for page in accounts.get("data", []):
        if str(page.get("id")) == str(page_id):
            token = page.get("access_token")
            if not token:
                raise GraphError(f"Page {page_id} has no access_token in /me/accounts.")
            return token
    # Pages owned by a business portfolio are often absent from /me/accounts;
    # fetch the Page directly instead.
    direct = _graph_get(
        f"{page_id}", {"fields": "access_token", "access_token": user_token}
    )
    if direct.get("access_token"):
        return direct["access_token"]
    raise GraphError(f"Page {page_id} not found in /me/accounts or via direct lookup.")


def get_ig_user_id(
    page_token: str, page_id: str, user_token: str | None = None
) -> str:
    """Resolve the Page's linked Instagram Business account id.

    Reading `/{page_id}` needs pages_read_engagement, which some otherwise
    valid tokens lack; fall back to the user token's /me/accounts listing.
    """
    try:
        payload = _graph_get(
            f"{page_id}",
            {"fields": "instagram_business_account{id,username}", "access_token": page_token},
        )
        ig = payload.get("instagram_business_account") or {}
        if ig.get("id"):
            return str(ig["id"])
    except GraphError as exc:
        if not user_token:
            raise
        log.warning("[Meta] Page lookup blocked (%s); using /me/accounts instead", str(exc)[:160])

    accounts = _graph_get(
        "me/accounts",
        {
            "fields": "id,name,instagram_business_account{id,username}",
            "limit": 100,
            "access_token": user_token or page_token,
        },
    )
    for page in accounts.get("data", []):
        if str(page.get("id")) == str(page_id):
            ig = page.get("instagram_business_account") or {}
            if ig.get("id"):
                return str(ig["id"])
    raise GraphError(f"No Instagram Business account linked to page {page_id}.")


def persist_meta_credentials(
    long_lived_user_token: str, page_token: str, page_id: str, ig_user_id: str
) -> None:
    """Write the permanent Meta values into .env."""
    update_env_file(
        {
            "META_ACCESS_TOKEN": long_lived_user_token,
            "FB_PAGE_ACCESS_TOKEN": page_token,
            "FB_PAGE_ID": str(page_id),
            "IG_USER_ID": str(ig_user_id),
        }
    )


def _prompt(label: str, secret: bool = False) -> str:
    """Prompt only on a real terminal; never block a scheduled/non-interactive run."""
    if os.getenv("RISHI_NONINTERACTIVE") == "1" or not sys.stdin.isatty():
        return ""
    try:
        return (getpass.getpass if secret else input)(label).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def prompt_meta_credentials() -> None:
    """Interactively fill any missing Meta app credentials, then persist them."""
    prompts = {
        "FB_APP_ID": ("Meta App ID: ", False),
        "FB_APP_SECRET": ("Meta App Secret: ", True),
        "META_ACCESS_TOKEN": ("Graph Explorer short-lived user token: ", True),
    }
    updates: dict[str, str] = {}
    for key, (label, secret) in prompts.items():
        if not os.getenv(key, "").strip():
            value = _prompt(label, secret=secret)
            if value:
                updates[key] = value
    if updates:
        update_env_file(updates)


def setup_supabase_credentials() -> dict[str, str]:
    """Prompt for Supabase URL / service-role key and persist them to .env."""
    url = os.getenv("SUPABASE_URL", "").strip() or _prompt("Supabase URL: ")
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip() or _prompt(
        "Supabase service role key: ", secret=True
    )
    if not (url and key):
        raise RuntimeError(
            "Supabase URL and service role key are required (non-interactive shell)."
        )
    update_env_file({"SUPABASE_URL": url, "SUPABASE_SERVICE_KEY": key})
    return {"SUPABASE_URL": url, "SUPABASE_SERVICE_KEY": "set"}


def setup_meta_credentials() -> dict[str, Any]:
    """Validate the token, upgrade it to 60 days, resolve page + IG ids, persist."""
    prompt_meta_credentials()
    token = os.getenv("META_ACCESS_TOKEN", "")
    page_id = os.getenv("FB_PAGE_ID", "")
    if not token:
        raise GraphError("META_ACCESS_TOKEN is empty — cannot authenticate Meta.")
    if not page_id:
        raise GraphError("FB_PAGE_ID is empty — cannot select a page.")

    info = test_meta_token(token)
    long_token = token
    if os.getenv("FB_APP_ID") and os.getenv("FB_APP_SECRET"):
        exchanged = exchange_long_lived_user_token(token)
        long_token = exchanged.get("access_token", token)
        log.info("[Meta] Exchanged for long-lived token (expires_in=%s)", exchanged.get("expires_in"))
    else:
        log.warning("[Meta] FB_APP_ID/FB_APP_SECRET missing; keeping supplied token as-is")

    page_token = get_page_access_token(long_token, page_id)
    ig_user_id = get_ig_user_id(page_token, page_id, user_token=long_token)
    persist_meta_credentials(long_token, page_token, page_id, ig_user_id)
    return {
        "user_id": info["me"].get("id"),
        "user_name": info["me"].get("name"),
        "page_id": page_id,
        "ig_user_id": ig_user_id,
        "long_lived": bool(os.getenv("FB_APP_ID") and os.getenv("FB_APP_SECRET")),
    }


# ---------------------------------------------------------------------------
# 3. Facebook Reels (3-phase)
# ---------------------------------------------------------------------------
def schedule_facebook_reel(
    video_path: str | Path,
    caption: str,
    publish_at_utc: datetime,
    page_id: str | None = None,
    page_access_token: str | None = None,
) -> dict[str, Any]:
    """Upload a local MP4 to a Page Reel and schedule it.

    Phase 1 start  -> video_id + upload_url
    Phase 2 upload -> binary transfer with offset/file_size headers
    Phase 3 finish -> SCHEDULED (or PUBLISHED when < 12 minutes remain)
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if publish_at_utc.tzinfo is None:
        raise ValueError("publish_at_utc must be timezone-aware")

    page_id = page_id or os.getenv("FB_PAGE_ID", "")
    token = page_access_token or os.getenv("FB_PAGE_ACCESS_TOKEN", "")
    if not (page_id and token):
        raise GraphError("FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN missing.")

    base_url = f"{GRAPH}/{page_id}/video_reels"

    # ---- Phase 1: initialize ----
    init_res = _graph_post(base_url, {"upload_phase": "start", "access_token": token})
    video_id = init_res.get("video_id")
    upload_url = init_res.get("upload_url")
    if not (video_id and upload_url):
        raise GraphError(f"Reel init failed: {init_res}", init_res)
    log.info("[Facebook] Upload session %s started", video_id)

    # ---- Phase 2: binary transfer ----
    data = video_path.read_bytes()
    size = len(data)
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(size),
        "Content-Type": "application/octet-stream",
    }
    resp = requests.post(upload_url, headers=headers, data=data, timeout=300)
    if resp.status_code >= 400:
        raise GraphError(
            f"Reel transfer failed ({resp.status_code}): {resp.text[:800]}", _json_or_text(resp)
        )
    log.info("[Facebook] Uploaded %.1f MB for %s", size / 1e6, video_id)

    # ---- Phase 3: finish (with near-term fallback) ----
    publish_utc = publish_at_utc.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    remaining = publish_utc - now
    state = "SCHEDULED"
    if remaining < FB_SCHEDULE_MIN_LEAD:
        log.warning(
            "[Facebook] Only %s remain (< 12 min); publishing immediately instead of scheduling",
            remaining,
        )
        state = "PUBLISHED"

    finish_payload: dict[str, Any] = {
        "access_token": token,
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": state,
        "description": caption[:5000],
    }
    if state == "SCHEDULED":
        finish_payload["scheduled_publish_time"] = int(publish_utc.timestamp())

    try:
        finish_res = _graph_post(base_url, finish_payload)
    except GraphError as exc:
        # Never turn a far-future schedule into an immediate live post.
        if state != "SCHEDULED" or remaining > timedelta(hours=1):
            raise
        log.warning("[Facebook] SCHEDULED rejected (%s); falling back to PUBLISHED", exc)
        state = "PUBLISHED"
        finish_payload.pop("scheduled_publish_time", None)
        finish_payload["video_state"] = "PUBLISHED"
        finish_res = _graph_post(base_url, finish_payload)

    log.info("[Facebook] Reel %s -> %s", video_id, state)
    return {"video_id": video_id, "state": state, "response": finish_res}


def publish_facebook_reel_now(
    video_path: str | Path, caption: str
) -> dict[str, Any]:
    """Convenience wrapper: publish a reel immediately."""
    return schedule_facebook_reel(
        video_path, caption, datetime.now(timezone.utc) + FB_SCHEDULE_MIN_LEAD
    )


# ---------------------------------------------------------------------------
# 4. Instagram Reels (Supabase staging + container polling)
# ---------------------------------------------------------------------------
def _supabase_client():
    from supabase import create_client

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
    if not (url and key):
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY missing.")
    return create_client(url, key)


def upload_reel_to_supabase(
    video_path: str | Path,
    bucket: str | None = None,
    expires_seconds: int = 6 * 3600,
) -> str:
    """Upload an MP4 to Supabase Storage and return a signed public URL."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    bucket = bucket or os.getenv("REELS_BUCKET", "reels-staging")
    client = _supabase_client()
    storage = client.storage

    try:
        storage.create_bucket(bucket)
        log.info("[Supabase] Created bucket %s", bucket)
    except Exception:
        pass  # already exists / no permission

    object_path = (
        f"reels/{datetime.now(timezone.utc):%Y%m%d}/{video_path.stem}-{uuid.uuid4().hex[:8]}"
        f"{video_path.suffix or '.mp4'}"
    )
    mime = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
    with video_path.open("rb") as fh:
        storage.from_(bucket).upload(
            object_path,
            fh.read(),
            {"content-type": mime, "upsert": "true", "cache-control": "3600"},
        )
    log.info("[Supabase] Uploaded %s to %s/%s", video_path.name, bucket, object_path)

    signed = storage.from_(bucket).create_signed_url(object_path, expires_seconds)
    url = ""
    if isinstance(signed, dict):
        url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url") or ""
    elif isinstance(signed, str):
        url = signed
    if url.startswith("/"):
        url = os.getenv("SUPABASE_URL", "").rstrip("/") + url
    if not url:
        url = storage.from_(bucket).get_public_url(object_path)
    if not url:
        raise RuntimeError(f"Could not create a public URL for {bucket}/{object_path}")
    return url


def publish_instagram_reel(
    video_path: str | Path,
    caption: str,
    ig_user_id: str | None = None,
    access_token: str | None = None,
    cover_url: str | None = None,
    bucket: str | None = None,
    poll_interval: int = 10,
    poll_timeout: int = 900,
) -> str:
    """Stage the MP4 in Supabase, create a REELS container, wait, publish.

    If video_path is an http(s) URL it is used directly instead of uploading.
    Returns the published Instagram media id.
    """
    ig_user_id = ig_user_id or os.getenv("IG_USER_ID", "")
    access_token = access_token or os.getenv("FB_PAGE_ACCESS_TOKEN", "")
    if not (ig_user_id and access_token):
        raise GraphError("IG_USER_ID / FB_PAGE_ACCESS_TOKEN missing.")

    if isinstance(video_path, str) and video_path.startswith(("http://", "https://")):
        public_url = video_path
    else:
        public_url = upload_reel_to_supabase(video_path, bucket=bucket)
        log.info("[Instagram] Staged reel at signed URL (%d chars)", len(public_url))

    payload: dict[str, Any] = {
        "media_type": "REELS",
        "video_url": public_url,
        "caption": caption[:2200],
        "share_to_feed": "true",
        "access_token": access_token,
    }
    if cover_url:
        payload["cover_url"] = cover_url

    container = _graph_post(f"{ig_user_id}/media", payload)
    creation_id = container.get("id")
    if not creation_id:
        raise GraphError(f"Instagram container creation failed: {container}", container)
    log.info("[Instagram] Container %s created", creation_id)

    deadline = time.time() + poll_timeout
    last_code = None
    while time.time() < deadline:
        status_res = _graph_get(
            str(creation_id), {"fields": "status_code,status", "access_token": access_token}
        )
        code = status_res.get("status_code")
        if code != last_code:
            log.info("[Instagram] Container %s status: %s", creation_id, code)
            last_code = code
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise GraphError(f"Instagram processing failed: {status_res}", status_res)
        time.sleep(poll_interval)
    else:
        raise TimeoutError(
            f"Instagram container {creation_id} not FINISHED within {poll_timeout}s "
            f"(last status: {last_code})"
        )

    pub_res = _graph_post(
        f"{ig_user_id}/media_publish",
        {"creation_id": str(creation_id), "access_token": access_token},
    )
    media_id = pub_res.get("id")
    if not media_id:
        raise GraphError(f"Instagram publish failed: {pub_res}", pub_res)
    log.info("[Instagram] Published reel %s", media_id)
    return str(media_id)
