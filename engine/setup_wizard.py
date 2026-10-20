"""Interactive RISHI credential setup.

Run this in a real terminal (double-click setup_wizard.cmd or run with python).
It cannot work through a non-interactive scheduler because it needs your secrets.

Order:
  1. YouTube  — import client_secret.json, then Chrome OAuth consent
  2. Meta     — App ID / App Secret / Graph Explorer token -> 60-day page token
  3. Supabase — URL + service role key, validated against Storage
  4. Optional — run the 14-day live batch schedule
"""
from __future__ import annotations

import getpass
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import social_publishers as sp  # noqa: E402

MARKER = BASE_DIR / "credentials" / ".setup_complete"


def _ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _secret(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def youtube_step() -> bool:
    print("\n--- 1. YOUTUBE ---------------------------------------------------")
    if sp.token_path().exists():
        try:
            info = sp.verify_youtube_token()
            print(f"[OK] Token valid for channel: {info['channel_title']} ({info['channel_id']})")
            return True
        except Exception as exc:
            print(f"[!] Existing token unusable: {exc}")

    secret = sp.client_secret_path()
    if not secret.exists():
        print("Google OAuth client file is missing:")
        print(f"  {secret}")
        print("Create/download it at https://console.cloud.google.com/apis/credentials")
        print("(OAuth client type: Desktop app; enable YouTube Data API v3.)")
        raw = input("Path to downloaded client_secret*.json (Enter to skip): ").strip().strip('"')
        if not raw:
            print("[SKIP] YouTube not configured.")
            return False
        src = Path(raw)
        if not src.exists():
            print(f"[X] File not found: {src}")
            return False
        secret.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, secret)
        print(f"[OK] Copied OAuth client to {secret}")

    try:
        path = sp.run_youtube_oauth_flow()  # opens Chrome for consent
        info = sp.verify_youtube_token()
        print(f"[OK] Token saved to {path}")
        print(f"[OK] Channel: {info['channel_title']} ({info['channel_id']})")
        return True
    except Exception as exc:
        print(f"[X] YouTube OAuth failed: {exc}")
        return False


def meta_step() -> bool:
    print("\n--- 2. META / FACEBOOK / INSTAGRAM -------------------------------")
    for attempt in range(1, 4):
        app_id = _ask("Meta App ID")
        app_secret = _secret("Meta App Secret")
        token = _secret("Graph Explorer short-lived user token")
        if not (app_id and app_secret and token):
            print("[X] All three values are required.")
            continue
        sp.update_env_file(
            {"FB_APP_ID": app_id, "FB_APP_SECRET": app_secret, "META_ACCESS_TOKEN": token}
        )
        try:
            info = sp.setup_meta_credentials()
            print(f"[OK] User: {info.get('user_name')} ({info.get('user_id')})")
            print(f"[OK] Page ID: {info['page_id']}")
            print(f"[OK] IG User ID: {info['ig_user_id']}")
            print(f"[OK] Long-lived exchange: {info['long_lived']}")
            return True
        except Exception as exc:
            print(f"[X] Attempt {attempt} failed: {exc}")
    print("[SKIP] Meta not configured.")
    return False


def supabase_step() -> bool:
    print("\n--- 3. SUPABASE --------------------------------------------------")
    current = sp.os.getenv("SUPABASE_URL", "")
    url = _ask("Supabase URL", current).strip().rstrip("/")
    key = _secret("Supabase service role key")
    if not (url and key):
        print("[SKIP] Supabase not configured.")
        return False
    sp.update_env_file({"SUPABASE_URL": url, "SUPABASE_SERVICE_KEY": key})
    try:
        from supabase import create_client

        client = create_client(url, key)
        buckets = client.storage.list_buckets()
        names = [b.name if hasattr(b, "name") else b.get("name", "") for b in buckets]
        print(f"[OK] Service key accepted. Buckets: {', '.join(n for n in names if n) or '(none)'}")
        if sp.os.getenv("REELS_BUCKET", "reels-staging") not in names:
            try:
                client.storage.create_bucket(sp.os.getenv("REELS_BUCKET", "reels-staging"))
                print(f"[OK] Created bucket {sp.os.getenv('REELS_BUCKET', 'reels-staging')}")
            except Exception as exc:
                print(f"[!] Could not create reels bucket: {exc}")
        return True
    except Exception as exc:
        print(f"[X] Supabase validation failed: {exc}")
        return False


def status() -> dict[str, bool]:
    caps = sp.credentials_status()
    return {
        "youtube": caps["youtube_token"],
        "meta": caps["fb_page_token"] and caps["ig_user_id"],
        "supabase": caps["supabase_service_key"],
    }


def main() -> int:
    print("=" * 70)
    print(" RISHI CREDENTIAL SETUP")
    print("=" * 70)

    youtube_step()
    meta_step()
    supabase_step()

    state = status()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(
        json.dumps(
            {**state, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n--- STATUS -------------------------------------------------------")
    for name, ok in state.items():
        print(f"  {name:<10} {'OK' if ok else 'MISSING'}")
    print(f"  marker: {MARKER}")

    if all(state.values()):
        print("\n[OK] All credentials present. You can run the live batch now.")
        answer = _ask("Type BATCH to schedule 28 uploads, or Enter to stop")
        if answer.upper() == "BATCH":
            try:
                import master_orchestrator as mo

                mo.setup_logging()
                return mo.cmd_batch(14, False, None)
            except Exception as exc:
                print(f"[X] Batch failed: {exc}")
                return 1
    else:
        print("\n[!] Live batch not started: one or more platforms are still unconfigured.")

    input("\nPress Enter to close...")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        raise SystemExit(130)
