#!/usr/bin/env python3
"""Xiaozhi emotion asset pack helper.

Phases:
  frames  - ingest A/B/C stills into frames/<emotion>/{a,b,c}.png @ SIZE
  stills  - also copy frame B (mid) into png/<emotion>.png for preview
  gifs    - stitch 3-frame Tamagotchi GIFs (cat still, props flip)
  approve / status
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

EMOTIONS = [
    "neutral",
    "happy",
    "laughing",
    "funny",
    "sad",
    "angry",
    "crying",
    "loving",
    "embarrassed",
    "surprised",
    "shocked",
    "thinking",
    "winking",
    "cool",
    "relaxed",
    "delicious",
    "kissy",
    "confident",
    "sleepy",
    "silly",
    "confused",
]

FRAME_KEYS = ("a", "b", "c")

DEFAULT_SIZE = 190
DEFAULT_DURATION_S = 2.0  # total loop; split across 3 frames
DEFAULT_COLORS = 64
ROOT = Path(__file__).resolve().parent
SPECS_PATH = ROOT / "emotion_frames.json"


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def character_dir(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip()).strip("-_")
    if not safe:
        raise SystemExit("character name is empty after sanitizing")
    return ROOT / safe


def load_manifest(char_dir: Path) -> dict:
    path = char_dir / "manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_manifest(char_dir: Path, data: dict) -> None:
    path = char_dir / "manifest.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_specs() -> dict:
    if not SPECS_PATH.exists():
        raise SystemExit(f"missing specs: {SPECS_PATH}")
    return json.loads(SPECS_PATH.read_text(encoding="utf-8"))


def ensure_layout(char_dir: Path) -> None:
    for sub in ("source", "raw", "frames", "png", "gif"):
        (char_dir / sub).mkdir(parents=True, exist_ok=True)


def fit_square(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def flatten_rgba(img: Image.Image, bg=(245, 235, 225)) -> Image.Image:
    if img.mode != "RGBA":
        return img.convert("RGB")
    base = Image.new("RGB", img.size, bg)
    base.paste(img, mask=img.split()[3])
    return base


def quantize_shared(frames_rgba: list[Image.Image], colors: int) -> list[Image.Image]:
    frames_rgb = [flatten_rgba(fr) for fr in frames_rgba]
    w, h = frames_rgb[0].size
    sheet = Image.new("RGB", (w * len(frames_rgb), h))
    for i, fr in enumerate(frames_rgb):
        sheet.paste(fr, (i * w, 0))
    quantized = sheet.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    return [
        quantized.crop((i * w, 0, (i + 1) * w, h)).copy()
        for i in range(len(frames_rgb))
    ]


def optimize_with_gifsicle(gif_path: Path, lossy: int = 40) -> None:
    gifsicle = which("gifsicle")
    if not gifsicle:
        return
    tmp = gif_path.with_suffix(".opt.gif")
    cmd = [
        gifsicle,
        "-O2",
        f"--lossy={lossy}",
        "--loopcount=0",
        "--no-warnings",
        str(gif_path),
        "-o",
        str(tmp),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    tmp.replace(gif_path)


def cmd_frames(args: argparse.Namespace) -> None:
    """Ingest raw A/B/C images into frames/<emotion>/{a,b,c}.png and mid preview png/."""
    char_dir = character_dir(args.name)
    ensure_layout(char_dir)
    size = args.size
    raw_dir = Path(args.raw_dir).expanduser().resolve() if args.raw_dir else char_dir / "raw"
    specs = load_specs()

    files: dict[str, dict] = {}
    missing: list[str] = []

    for emotion in EMOTIONS:
        emo_dir = char_dir / "frames" / emotion
        emo_dir.mkdir(parents=True, exist_ok=True)
        frame_rel: dict[str, str] = {}
        ok = True
        for key in FRAME_KEYS:
            candidates = [
                raw_dir / f"{emotion}_{key}.png",
                raw_dir / f"{emotion}_{key.upper()}.png",
                raw_dir / emotion / f"{key}.png",
                char_dir / "raw" / f"{emotion}_{key}.png",
            ]
            src = next((p for p in candidates if p.exists()), None)
            if src is None:
                missing.append(f"{emotion}/{key}")
                ok = False
                continue
            out = emo_dir / f"{key}.png"
            with Image.open(src) as im:
                fit_square(im, size).save(out, format="PNG", optimize=True)
            frame_rel[key] = f"frames/{emotion}/{key}.png"
            print(f"  wrote {out}")

        if ok:
            # Mid frame as static preview
            preview = char_dir / "png" / f"{emotion}.png"
            shutil.copy2(emo_dir / "b.png", preview)
            files[emotion] = {
                "frames": frame_rel,
                "png": f"png/{emotion}.png",
                "gif": None,
                "focus": specs.get("emotions", {}).get(emotion, {}).get("focus"),
            }

    if missing:
        print(f"\nMissing frames: {', '.join(missing)}", file=sys.stderr)

    manifest = load_manifest(char_dir)
    manifest.update(
        {
            "character": char_dir.name,
            "size": [size, size],
            "emotions": EMOTIONS,
            "style": "tamagotchi-3frame",
            "approved": False,
            "source_image": "source/reference.png",
            "files": {**manifest.get("files", {}), **files},
            "specs": str(SPECS_PATH.name),
            "note": "Approve then: gifs --name <char> --duration 2",
        }
    )
    save_manifest(char_dir, manifest)
    print(f"\nReady emotions with A/B/C: {len(files)}/{len(EMOTIONS)}")


def cmd_approve(args: argparse.Namespace) -> None:
    char_dir = character_dir(args.name)
    manifest = load_manifest(char_dir)
    if not manifest:
        raise SystemExit(f"no manifest at {char_dir}")
    incomplete = []
    for emotion in EMOTIONS:
        for key in FRAME_KEYS:
            if not (char_dir / "frames" / emotion / f"{key}.png").exists():
                incomplete.append(f"{emotion}/{key}")
    if incomplete:
        raise SystemExit(f"cannot approve; missing: {', '.join(incomplete)}")
    manifest["approved"] = True
    save_manifest(char_dir, manifest)
    print(f"Approved {char_dir.name}. Run: gifs --name {char_dir.name}")


def make_tamagotchi_gif(
    frame_paths: list[Path],
    gif_path: Path,
    *,
    duration_s: float,
    colors: int,
) -> None:
    """Stitch N stills into a flipbook GIF; cat stays still (no bob)."""
    rgba = []
    for p in frame_paths:
        with Image.open(p) as im:
            rgba.append(fit_square(im, DEFAULT_SIZE).convert("RGBA"))
    n = len(rgba)
    delay_ms = max(1, int(round((duration_s * 1000) / n)))
    out_frames = quantize_shared(rgba, colors=colors)
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    out_frames[0].save(
        gif_path,
        save_all=True,
        append_images=out_frames[1:],
        duration=delay_ms,
        loop=0,
        disposal=1,
        optimize=False,
    )
    optimize_with_gifsicle(gif_path)


def cmd_gifs(args: argparse.Namespace) -> None:
    char_dir = character_dir(args.name)
    manifest = load_manifest(char_dir)
    if not manifest.get("approved") and not args.force:
        raise SystemExit(
            f"{char_dir.name} not approved. Run: approve --name {char_dir.name}"
        )

    gif_dir = char_dir / "gif"
    gif_dir.mkdir(parents=True, exist_ok=True)
    files = manifest.get("files", {})
    total_bytes = 0
    done = 0

    for emotion in EMOTIONS:
        # Seamless ping-pong: A→B→C→B → (loop) A…
        keyed = {k: char_dir / "frames" / emotion / f"{k}.png" for k in FRAME_KEYS}
        if not all(p.exists() for p in keyed.values()):
            print(f"  skip incomplete {emotion}", file=sys.stderr)
            continue
        paths = [keyed["a"], keyed["b"], keyed["c"], keyed["b"]]
        gif_path = gif_dir / f"{emotion}.gif"
        make_tamagotchi_gif(
            paths,
            gif_path,
            duration_s=args.duration,
            colors=args.colors,
        )
        size = gif_path.stat().st_size
        total_bytes += size
        entry = files.get(emotion, {})
        entry["gif"] = f"gif/{emotion}.gif"
        entry["gif_bytes"] = size
        entry["frames"] = {k: f"frames/{emotion}/{k}.png" for k in FRAME_KEYS}
        files[emotion] = entry
        done += 1
        print(f"  wrote {gif_path} ({size / 1024:.1f} KB)")

    manifest["files"] = files
    manifest["gif_settings"] = {
        "mode": "tamagotchi-ABCBA",
        "sequence": ["a", "b", "c", "b"],
        "duration_s": args.duration,
        "delay_ms_per_frame": int(round((args.duration * 1000) / 4)),
        "colors": args.colors,
        "loop": 0,
        "gifsicle": which("gifsicle") is not None,
    }
    save_manifest(char_dir, manifest)
    print(f"\nDone. {done} GIFs ({total_bytes / 1024:.1f} KB total)")


def cmd_status(args: argparse.Namespace) -> None:
    char_dir = character_dir(args.name)
    manifest = load_manifest(char_dir)
    if not manifest:
        raise SystemExit(f"no pack at {char_dir}")
    frame_n = 0
    for e in EMOTIONS:
        if all((char_dir / "frames" / e / f"{k}.png").exists() for k in FRAME_KEYS):
            frame_n += 1
    gif_n = sum(1 for e in EMOTIONS if (char_dir / "gif" / f"{e}.gif").exists())
    print(
        json.dumps(
            {
                "character": manifest.get("character"),
                "style": manifest.get("style"),
                "size": manifest.get("size"),
                "approved": manifest.get("approved"),
                "complete_abc": f"{frame_n}/{len(EMOTIONS)}",
                "gif_count": f"{gif_n}/{len(EMOTIONS)}",
                "path": str(char_dir),
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Xiaozhi emotion asset pack helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("frames", help="Ingest A/B/C stills into frames/ and png/ preview")
    f.add_argument("--name", required=True)
    f.add_argument("--raw-dir", help="Directory containing <emotion>_a.png etc.")
    f.add_argument("--size", type=int, default=DEFAULT_SIZE)
    f.set_defaults(func=cmd_frames)

    a = sub.add_parser("approve", help="Mark pack approved after reviewing frames/")
    a.add_argument("--name", required=True)
    a.set_defaults(func=cmd_approve)

    g = sub.add_parser("gifs", help="Build 3-frame Tamagotchi GIFs")
    g.add_argument("--name", required=True)
    g.add_argument("--duration", type=float, default=DEFAULT_DURATION_S, help="Total loop seconds")
    g.add_argument("--colors", type=int, default=DEFAULT_COLORS)
    g.add_argument("--force", action="store_true")
    g.set_defaults(func=cmd_gifs)

    t = sub.add_parser("status", help="Show pack status")
    t.add_argument("--name", required=True)
    t.set_defaults(func=cmd_status)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
