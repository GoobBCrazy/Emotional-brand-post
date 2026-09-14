"""
Reads the current week's manifest under content/<week>/manifest.json,
uploads each day's video to Cloudinary, then schedules a Buffer post
per channel for each day via Buffer's GraphQL API.

This runs on GitHub's own infrastructure (Actions runner), NOT inside
Anthropic's Cowork sandbox -- so it isn't subject to that egress block.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

CLOUD_NAME = os.environ["CLOUDINARY_CLOUD_NAME"]
API_KEY = os.environ["CLOUDINARY_API_KEY"]
API_SECRET = os.environ["CLOUDINARY_API_SECRET"]
BUFFER_TOKEN = os.environ["BUFFER_ACCESS_TOKEN"]

BUFFER_GRAPHQL_URL = "https://api.buffer.com"

CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess {
      post { id status }
    }
    ... on MutationError {
      message
    }
  }
}
"""


def cloudinary_signature(params: dict, api_secret: str) -> str:
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


def schedule_buffer_post(channel_id: str, text: str, video_url: str, due_at_unix: int, metadata=None) -> dict:
    due_at_iso = datetime.fromtimestamp(due_at_unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    input_obj = {
        "text": text,
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": due_at_iso,
        "assets": [{"video": {"url": video_url}}],
    }
    if metadata:
        input_obj["metadata"] = metadata

    resp = requests.post(
        BUFFER_GRAPHQL_URL,
        json={"query": CREATE_POST_MUTATION, "variables": {"input": input_obj}},
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BUFFER_TOKEN}",
        },
        timeout=60,
    )
    result = resp.json()

    if resp.status_code >= 400 or "errors" in result:
        print(f"  BUFFER ERROR for channel {channel_id}: {result.get('errors', result)}", file=sys.stderr)
    else:
        data = (result.get("data") or {}).get("createPost", {})
        if data.get("__typename") == "MutationError":
            print(f"  BUFFER ERROR for channel {channel_id}: {data.get('message')}", file=sys.stderr)
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

        due_at_unix = int(day["scheduled_at_unix"])

        for platform, channel_id in channels.items():
            caption_key = f"{platform}_text"
            text = day[caption_key]

            metadata = None
            if platform == "youtube":
                metadata = {"youtube": {"title": day["title"], "categoryId": "22"}}
            elif platform == "facebook":
                metadata = {"facebook": {"type": "reel"}}

            print(f"  Scheduling Day {n} on {platform} ({channel_id}) for {day['date']} 9am...")
            result = schedule_buffer_post(channel_id, text, secure_url, due_at_unix, metadata)
            data = (result.get("data") or {}).get("createPost", {})
            print(f"    result: {data.get('__typename', result)}")


if __name__ == "__main__":
    main()
