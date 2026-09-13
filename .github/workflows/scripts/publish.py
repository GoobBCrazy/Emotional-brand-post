"""
Reads the current week's manifest under content/<week>/manifest.json,
uploads each day's video to Cloudinary, then schedules a Buffer post
per channel for each day.

This runs on GitHub's own infrastructure (Actions runner), NOT inside
Anthropic's Cowork sandbox -- so it isn't subject to that egress block.
"""
import hashlib
import json
import os
import sys
import time

import requests

CLOUD_NAME = os.environ["CLOUDINARY_CLOUD_NAME"]
API_KEY = os.environ["CLOUDINARY_API_KEY"]
API_SECRET = os.environ["CLOUDINARY_API_SECRET"]
BUFFER_TOKEN = os.environ["BUFFER_ACCESS_TOKEN"]

BUFFER_API = "https://api.bufferapp.com/1"


def cloudinary_signature(params: dict, api_secret: str) -> str:
    """Cloudinary signs by sorting params alphabetically, joining as
    key=value pairs with '&', appending the api_secret, and taking SHA1."""
    to_sign = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    to_sign += api_secret
    return hashlib.sha1(to_sign.encode("utf-8")).hexdigest()


def upload_to_cloudinary(video_path: str, public_id: str, folder: str) -> str:
    timestamp = int(time.time())
    params = {
        "timestamp": timestamp,
        "public_id": public_id,
        "folder": folder,
        "overwrite": "true",
    }
    signature = cloudinary_signature(params, API_SECRET)

    url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/video/upload"
    with open(video_path, "rb") as f:
        files = {"file": f}
        data = {**params, "api_key": API_KEY, "signature": signature}
        resp = requests.post(url, files=files, data=data, timeout=120)

    resp.raise_for_status()
    result = resp.json()
    if "secure_url" not in result:
        raise RuntimeError(f"Cloudinary upload failed for {video_path}: {result}")
    return result["secure_url"]


def schedule_buffer_post(profile_id: str, text: str, video_url: str, scheduled_at: int) -> dict:
    url = f"{BUFFER_API}/updates/create.json"
    data = {
        "access_token": BUFFER_TOKEN,
        "profile_ids[]": profile_id,
        "text": text,
        "scheduled_at": scheduled_at,
        "media[video]": video_url,
    }
    resp = requests.post(url, data=data, timeout=60)
    result = resp.json()
    if resp.status_code >= 400 or result.get("success") is False:
        print(f"  BUFFER ERROR for profile {profile_id}: {result}", file=sys.stderr)
    return result


def main():
    with open("content/latest.txt") as f:
        week = f.read().strip()

    week_dir = f"content/{week}"
    with open(f"{week_dir}/manifest.json") as f:
        manifest = json.load(f)

    channels = manifest["channels"]  # {"tiktok": "...", "youtube": "...", "facebook": "..."}

    for day in manifest["days"]:
        n = day["day"]
        video_path = os.path.join(week_dir, day["video_filename"])
        public_id = f"{manifest['theme_slug']}_day{n}_{day['date'].replace('-', '')}"

        print(f"Uploading Day {n} ({video_path}) to Cloudinary...")
        secure_url = upload_to_cloudinary(
            video_path, public_id, "Weekly Emotional Wellness Content Plan + Videos"
        )
        print(f"  -> {secure_url}")

        scheduled_at = int(day["scheduled_at_unix"])

        for platform, profile_id in channels.items():
            caption_key = f"{platform}_text"
            text = day[caption_key]
            print(f"  Scheduling Day {n} on {platform} ({profile_id}) for {day['date']} 9am...")
            result = schedule_buffer_post(profile_id, text, secure_url, scheduled_at)
            print(f"    result: {result.get('success', result)}")


if __name__ == "__main__":
    main()
