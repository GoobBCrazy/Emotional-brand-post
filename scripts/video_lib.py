"""
Motion-graphic short-video generator, v2.

Pulls a real Pexels video clip matching the day's mood as the moving
background (falling back to a procedural gradient if Pexels has nothing
suitable), overlays quote/script lines with a high-contrast text panel,
and lays down a generated ambient audio bed.

Runs on GitHub Actions' own infrastructure -- open internet, unlike
Anthropic's Cowork sandbox where this pipeline used to run.
"""
import hashlib
import math
import os
import subprocess
import wave

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1920
FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "Lora-Variable.ttf")

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# Fallback procedural background (used only if a Pexels clip can't be fetched)
# ---------------------------------------------------------------------------

def make_gradient(size, color_top, color_bottom):
    w, h = size
    top = np.array(color_top, dtype=np.float32)
    bottom = np.array(color_bottom, dtype=np.float32)
    ys = np.linspace(0, 1, h).reshape(h, 1, 1)
    grad = top.reshape(1, 1, 3) * (1 - ys) + bottom.reshape(1, 1, 3) * ys
    grad = np.repeat(grad, w, axis=1).astype(np.uint8)
    return Image.fromarray(grad, "RGB")


def add_soft_clouds(img, seed=0, opacity=55):
    rng = np.random.default_rng(seed)
    w, h = img.size
    noise = rng.normal(0, 1, (h // 6, w // 6))
    big = Image.fromarray(((noise - noise.min()) / (noise.max() - noise.min()) * 255).astype(np.uint8))
    big = big.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(60))
    arr = np.array(big)
    alpha = (arr.astype(np.float32) / 255.0 * opacity).astype(np.uint8)
    white = np.zeros((h, w, 4), dtype=np.uint8)
    white[..., 0:3] = 255
    white[..., 3] = alpha
    cloud_layer = Image.fromarray(white, "RGBA")
    return Image.alpha_composite(img.convert("RGBA"), cloud_layer).convert("RGB")


def render_fallback_bg(path, top_hex, bottom_hex, seed):
    big = make_gradient((int(W * 1.35), int(H * 1.35)), hex2rgb(top_hex), hex2rgb(bottom_hex))
    big = add_soft_clouds(big, seed=seed, opacity=55)
    vig = Image.new("L", big.size, 0)
    vd = ImageDraw.Draw(vig)
    vd.ellipse([-big.size[0] * 0.3, -big.size[1] * 0.2, big.size[0] * 1.3, big.size[1] * 1.2], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(200))
    dark = Image.new("RGB", big.size, (20, 16, 12))
    big = Image.composite(big, dark, vig)
    big.save(path, "PNG")


# ---------------------------------------------------------------------------
# Real footage: Pexels video search + download
# ---------------------------------------------------------------------------

def fetch_pexels_video(term, api_key, out_path, min_width=720, timeout=60):
    """Search Pexels for a portrait clip matching `term` and download it to
    `out_path`. Returns out_path on success, None on any failure (caller
    should fall back to the procedural gradient background)."""
    headers = {"Authorization": api_key}
    try:
        resp = requests.get(
            PEXELS_SEARCH_URL,
            params={"query": term, "per_page": 6, "orientation": "portrait"},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])

        if not videos:
            resp = requests.get(
                PEXELS_SEARCH_URL,
                params={"query": term, "per_page": 6},
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])

        if not videos:
            print(f"  [pexels] no results for '{term}'")
            return None

        video = videos[0]
        files = sorted(
            [f for f in video.get("video_files", []) if f.get("link")],
            key=lambda f: f.get("width") or 0,
        )
        chosen = next((f for f in files if (f.get("width") or 0) >= min_width), None)
        if not chosen and files:
            chosen = files[-1]
        if not chosen:
            print(f"  [pexels] no downloadable file for '{term}'")
            return None

        with requests.get(chosen["link"], stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        return out_path
    except Exception as e:
        print(f"  [pexels] fetch failed for '{term}': {e}")
        return None


# ---------------------------------------------------------------------------
# Text overlay rendering (high-contrast: solid scrim panel, not just a blur)
# ---------------------------------------------------------------------------

def wrap_text(text, font, max_w, draw):
    words = text.split()
    lines, cur = [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def render_text_png(path, text, size=80, accent_color=(255, 250, 240), font_path=None):
    TEXT_COLOR = (250, 248, 244, 255)
    accent = (*accent_color, 255) if len(accent_color) == 3 else accent_color

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path or FONT_PATH, size)
    max_w = int(W * 0.82)
    lines = wrap_text(text, font, max_w, draw)
    line_h = int(size * 1.32)
    total_h = line_h * len(lines)
    y = (H - total_h) // 2

    max_line_w = 0
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        max_line_w = max(max_line_w, bbox[2] - bbox[0])

    pad_x, pad_y = 56, 40
    panel_left = (W - max_line_w) // 2 - pad_x
    panel_right = (W + max_line_w) // 2 + pad_x
    panel_top = y - pad_y
    panel_bottom = y + total_h + pad_y

    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim)
    sdraw.rounded_rectangle(
        [panel_left, panel_top, panel_right, panel_bottom],
        radius=28,
        fill=(18, 15, 20, 158),
    )
    sdraw.rounded_rectangle(
        [panel_left, panel_top, panel_right, panel_top + 6],
        radius=3,
        fill=accent,
    )
    img = Image.alpha_composite(img, scrim)

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sdraw2 = ImageDraw.Draw(shadow)
    for i, ln in enumerate(lines):
        bbox = draw.textbbox((0, 0), ln, font=font)
        lw = bbox[2] - bbox[0]
        x = (W - lw) // 2
        sdraw2.text((x, y + i * line_h + 3), ln, font=font, fill=(0, 0, 0, 200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    img = Image.alpha_composite(img, shadow)

    draw = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        bbox = draw.textbbox((0, 0), ln, font=font)
        lw = bbox[2] - bbox[0]
        x = (W - lw) // 2
        draw.text((x, y + i * line_h), ln, font=font, fill=TEXT_COLOR)
    img.save(path, "PNG")


# ---------------------------------------------------------------------------
# Ambient audio bed (kept procedural -- no licensing questions)
# ---------------------------------------------------------------------------

def make_ambient_audio(path, duration, seed=0):
    sr = 44100
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    rng = np.random.default_rng(seed)
    base_freqs = [110.0, 164.81, 220.0]
    audio = np.zeros(n)
    for f in base_freqs:
        drift = 1 + 0.0015 * np.sin(2 * math.pi * 0.05 * t + rng.uniform(0, 6))
        audio += np.sin(2 * math.pi * f * drift * t) * (0.18 / len(base_freqs))
    noise = rng.normal(0, 1, n)
    b = np.ones(200) / 200
    shimmer = np.convolve(noise, b, mode="same") * 0.02
    audio += shimmer
    fade_len = int(sr * 2.0)
    env = np.ones(n)
    env[:fade_len] = np.linspace(0, 1, fade_len)
    env[-fade_len:] = np.linspace(1, 0, fade_len)
    audio *= env
    audio = np.clip(audio, -1, 1)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


# ---------------------------------------------------------------------------
# Main video assembly
# ---------------------------------------------------------------------------

def build_video(
    out_path,
    lines,
    top_hex,
    bottom_hex,
    seed=0,
    sec_per_line=3.4,
    fps=30,
    motion="zoom_in",
    accent_color=(255, 250, 240),
    font_path=None,
    pexels_term=None,
    pexels_api_key=None,
):
    work = os.path.dirname(out_path) or "."
    os.makedirs(work, exist_ok=True)
    audio_path = os.path.join(work, f"_audio_{seed}.wav")

    intro_pad = 1.0
    outro_pad = 2.0
    duration = intro_pad + sec_per_line * len(lines) + outro_pad
    make_ambient_audio(audio_path, duration, seed=seed)
    total_frames = int(duration * fps)

    bg_video_path = None
    if pexels_term and pexels_api_key:
        candidate = os.path.join(work, f"_pexels_{seed}.mp4")
        bg_video_path = fetch_pexels_video(pexels_term, pexels_api_key, candidate)

    if bg_video_path:
        print(f"  Using real Pexels footage for background (term: '{pexels_term}')")
        bg_inputs = ["-stream_loop", "-1", "-i", bg_video_path]
        bg_filter = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,fps={fps},"
            f"eq=brightness=-0.03:saturation=1.05,format=yuv420p[bg]"
        )
    else:
        print(f"  Falling back to procedural gradient background (term: '{pexels_term}')")
        bg_path = os.path.join(work, f"_bg_{seed}.png")
        render_fallback_bg(bg_path, top_hex, bottom_hex, seed)
        if motion == "zoom_out":
            z_start, z_end = 1.18, 1.0
        else:
            z_start, z_end = 1.0, 1.18
        zoom_expr = f"'{z_start}+({z_end}-{z_start})*on/{total_frames}'"
        bg_inputs = ["-loop", "1", "-i", bg_path]
        bg_filter = (
            f"[0:v]scale=-2:{int(H*1.4)},zoompan=z={zoom_expr}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={W}x{H}:fps={fps},format=yuv420p[bg]"
        )

    text_paths = []
    for i, ln in enumerate(lines):
        tp = os.path.join(work, f"_txt_{seed}_{i:02d}.png")
        render_text_png(tp, ln, accent_color=accent_color, font_path=font_path)
        text_paths.append(tp)

    inputs = bg_inputs[:]
    for tp in text_paths:
        inputs += ["-loop", "1", "-i", tp]
    inputs += ["-i", audio_path]

    filter_parts = [bg_filter]
    prev = "bg"
    for i, _ in enumerate(text_paths):
        start = intro_pad + i * sec_per_line
        end = start + sec_per_line
        fade_d = 0.5
        idx = i + 1
        filter_parts.append(
            f"[{idx}:v]format=rgba,fade=t=in:st={start}:d={fade_d}:alpha=1,"
            f"fade=t=out:st={end-fade_d}:d={fade_d}:alpha=1[t{i}]"
        )
        nxt = f"v{i}"
        filter_parts.append(f"[{prev}][t{i}]overlay=0:0:enable='between(t,{start},{end})'[{nxt}]")
        prev = nxt

    filter_complex = ";".join(filter_parts)
    audio_idx = len(text_paths) + 1

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev}]", "-map", f"{audio_idx}:a",
        "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])
    return out_path
