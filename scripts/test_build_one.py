"""
Standalone test: builds ONE day's video using real Pexels footage and saves
it locally as an artifact -- does NOT touch Cloudinary or Buffer. Safe to
run any time without risk of duplicate posts.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from video_lib import build_video, hex2rgb  # noqa: E402

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
TEST_DAY = int(os.environ.get("TEST_DAY", "1"))


def main():
    with open("content/latest.txt") as f:
        week = f.read().strip()

    week_dir = f"content/{week}"
    with open(f"{week_dir}/manifest.json") as f:
        manifest = json.load(f)

    day = next(d for d in manifest["days"] if d["day"] == TEST_DAY)
    lines = [day["featured_quote"]] + day["script_lines"] + [day["cta_bundle"], day["cta_reply"]]

    out_path = f"test_day{TEST_DAY}.mp4"
    print(f"Building test video for Day {TEST_DAY} ({day['subtheme']})...")
    build_video(
        out_path=out_path,
        lines=lines,
        top_hex=day["palette_top"],
        bottom_hex=day["palette_bottom"],
        seed=day["day"],
        sec_per_line=3.4,
        fps=30,
        motion=day["motion"],
        accent_color=hex2rgb(day["text_color_hex"]),
        pexels_term=day.get("pexels_term"),
        pexels_api_key=PEXELS_API_KEY,
    )
    print(f"Done: {out_path}")


if __name__ == "__main__":
    main()
