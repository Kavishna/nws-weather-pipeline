"""
Run this ONCE, locally, before your first deploy - it creates the
`destinations` documents in Firestore (Discord filled in, Facebook/
Instagram/TikTok left as empty placeholders per state). The deployed
Cloud Run service (app.py) does NOT run this - it assumes destinations
already exist and just calls run_alert_pipeline().

Prerequisites:
    pip install google-cloud-firestore
    gcloud auth application-default login
    gcloud config set project YOUR_PROJECT_ID
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

Usage:
    python bootstrap_firestore.py
"""
import os
from nws_mapbox_generator import TARGET_STATES, bootstrap_destination, get_firestore_client

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

if not DISCORD_WEBHOOK_URL:
    raise SystemExit("Set DISCORD_WEBHOOK_URL in your environment before running this.")

db = get_firestore_client()

for state in TARGET_STATES:
    bootstrap_destination(db, state, "discord", {"discord_webhook_url": DISCORD_WEBHOOK_URL})
    bootstrap_destination(db, state, "facebook", {"fb_page_id": "", "fb_access_token": ""})
    bootstrap_destination(db, state, "instagram", {"ig_user_id": "", "ig_access_token": ""})
    bootstrap_destination(db, state, "tiktok", {"tiktok_access_token": ""})

print(f"\nDone. {len(TARGET_STATES)} state destinations ready in Firestore.")
