"""
fetch_manifest.py
Reads the weekly "MANIFEST <week-start> DAY <n>" ideas that the Cowork routine
saves in Buffer, assembles them into content/<week-start>/manifest.json, and
points content/latest.txt at that folder so scripts/publish.py can run.

Writes found=true/false (and week=<date>) to $GITHUB_OUTPUT.
"""
import json
import os
import re
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API_URL = "https://api.buffer.com"
ORG_ID = os.environ.get("BUFFER_ORG_ID", "64b41097de92840a4c961bf8")
TOKEN = os.environ.get("BUFFER_ACCESS_TOKEN", "")
POST_TZ = ZoneInfo("America/Chicago")
POST_TIME = time(9, 0)  # 9:00 AM Central, automatically adjusts for daylight saving

CHANNELS = {
    "tiktok": "67d6575a1616c536dd0ac24d",
    "youtube": "64b41160747134a10bb82084",
    "facebook": "68614758acfb098c69acc6b6",
}

REQUIRED = [
    "subtheme", "title", "featured_quote", "script_lines", "cta_bundle",
    "cta_reply", "pexels_term", "palette_top", "palette_bottom",
    "text_color_hex", "motion", "tiktok_text", "youtube_text", "facebook_text",
]

TITLE_RE = re.compile(r"^\s*MANIFEST\s+(\d{4}-\d{2}-\d{2})\s+DAY\s+([1-7])\s*$", re.I)

QUERY = """
query Ideas($org: OrganizationId!, $after: String) {
  ideas(first: 50, after: $after, input: { organizationId: $org }) {
    edges { node { id content { title text } } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def set_output(**kwargs):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in kwargs.items())
    if path:
        with open(path, "a") as fh:
            fh.write(lines)
    print(lines, end="")


def fetch_ideas():
    if not TOKEN:
        sys.exit("BUFFER_ACCESS_TOKEN is not set.")
    ideas, after = [], None
    while True:
        resp = requests.post(
            API_URL,
            json={"query": QUERY, "variables": {"org": ORG_ID, "after": after}},
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            sys.exit(f"Buffer API error: {body['errors']}")
        conn = body["data"]["ideas"]
        ideas += [e["node"] for e in conn["edges"]]
        if not conn["pageInfo"]["hasNextPage"]:
            return ideas
        after = conn["pageInfo"]["endCursor"]


def parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # tolerate code fences
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def group_weeks(ideas):
    weeks = {}
    for idea in ideas:
        m = TITLE_RE.match(idea["content"].get("title") or "")
        if not m:
            continue
        week, day = m.group(1), int(m.group(2))
        try:
            data = parse_json(idea["content"].get("text"))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"WARNING: could not read JSON in '{idea['content']['title']}': {exc}")
            continue
        weeks.setdefault(week, {})[day] = data
    return weeks


def build_manifest(week, days):
    start = date.fromisoformat(week)
    theme_slug = None
    out_days = []
    for n in range(1, 8):
        d = days[n]
        missing = [k for k in REQUIRED if not d.get(k)]
        if missing:
            raise ValueError(f"day {n} is missing: {', '.join(missing)}")
        if len(d["script_lines"]) < 5:
            raise ValueError(f"day {n} has only {len(d['script_lines'])} script lines")
        theme_slug = theme_slug or d.get("theme_slug")
        post_date = date.fromordinal(start.toordinal() + n - 1)
        when = datetime.combine(post_date, POST_TIME, tzinfo=POST_TZ)
        entry = {"day": n, "date": post_date.isoformat()}
        entry.update({k: d[k] for k in REQUIRED})
        entry["motion"] = "zoom_out" if "out" in str(d["motion"]).lower() else "zoom_in"
        entry["scheduled_at_unix"] = int(when.timestamp())
        out_days.append(entry)
    return {
        "theme_slug": theme_slug or "weekly-content",
        "channels": CHANNELS,
        "days": out_days,
    }


def main():
    content = Path("content")
    today = datetime.now(POST_TZ).date()
    weeks = group_weeks(fetch_ideas())
    print(f"Found manifest ideas for weeks: {sorted(weeks) or 'none'}")

    # Soonest upcoming week that is complete and hasn't been published yet.
    for week in sorted(weeks):
        if date.fromisoformat(week) <= today:
            print(f"Skipping {week}: not in the future.")
            continue
        if (content / week).exists():
            print(f"Skipping {week}: content/{week} already exists (already published).")
            continue
        days = weeks[week]
        if sorted(days) != list(range(1, 8)):
            print(f"Skipping {week}: only days {sorted(days)} found, need 1-7.")
            continue
        manifest = build_manifest(week, days)
        folder = content / week
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        (content / "latest.txt").write_text(week + "\n")
        print(f"Wrote content/{week}/manifest.json ({manifest['theme_slug']}).")
        set_output(found="true", week=week)
        return

    set_output(found="false", week="")
    print("Nothing new to publish.")


if __name__ == "__main__":
    main()
