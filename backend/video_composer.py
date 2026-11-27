"""FFmpeg video stitcher, CJK subtitle generator (.srt), and Pillow+Overlay hard-subtitle burner.

Uses Pillow to render crisp anti-aliased Chinese subtitle PNGs and FFmpeg's universal
``overlay`` filter so hard-subtitles work reliably even on FFmpeg builds without
``libass`` or ``libfreetype``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


def _require_ffmpeg() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("未检测到 ffmpeg / ffprobe，请先安装（macOS: brew install ffmpeg）。")
    return ffmpeg, ffprobe


def detect_cjk_font_path() -> Optional[str]:
    """Finds a reliable CJK TrueType/TTC font file on macOS / Linux / Windows."""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _render_subtitle_png(text: str, out_png: Path, *, width: int, height: int) -> Path:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_size = max(24, int(height * 0.048))
    font_path = detect_cjk_font_path()
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Wrap long Chinese lines gracefully
    max_chars = max(14, int((width * 0.84) / font_size))
    lines: list[str] = []
    for raw_line in text.strip().splitlines():
        cur = raw_line.strip()
        while len(cur) > max_chars:
            lines.append(cur[:max_chars])
            cur = cur[max_chars:]
        if cur:
            lines.append(cur)
    if not lines:
        img.save(out_png)
        return out_png

    line_bboxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_heights = [max(font_size, b[3] - b[1]) for b in line_bboxes]
    line_widths = [b[2] - b[0] for b in line_bboxes]
    line_gap = 8
    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    max_w = max(line_widths)

    pad_x, pad_y = 22, 12
    box_w = max_w + pad_x * 2
    box_h = total_h + pad_y * 2
    box_x0 = (width - box_w) // 2
    box_y0 = height - box_h - int(height * 0.055)
    draw.rounded_rectangle(
        [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h],
        radius=12,
        fill=(10, 10, 14, 185),
    )

    y = box_y0 + pad_y
    for ln, lw, lh in zip(lines, line_widths, line_heights):
        x = (width - lw) // 2
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 220))
        y += lh + line_gap

    img.save(out_png)
    return out_png


def probe_duration(video_path: Path) -> float:
    _ffmpeg, ffprobe = _require_ffmpeg()
    out = subprocess.check_output(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        text=True,
    ).strip()
    try:
        return max(0.5, float(out))
    except ValueError:
        return 3.0


def _fmt_srt_ts(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    h = ms_total // 3600000
    m = (ms_total % 3600000) // 60000
    s = (ms_total % 60000) // 1000
    ms = ms_total % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt_file(segments: list[tuple[float, float, str]], srt_path: Path) -> Path:
    lines: list[str] = []
    idx = 1
    for start, end, text in segments:
        cleaned = text.strip()
        if not cleaned:
            continue
        lines.append(f"{idx}\n{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n{cleaned}\n")
        idx += 1
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    return srt_path


def generate_mock_clip(
    output_path: Path,
    *,
    shot_index: int,
    duration_s: float = 2.5,
    width: int = 640,
    height: int = 384,
) -> Path:
    """Generates a real playable H.264 MP4 test clip in Mock mode using ffmpeg."""
    ffmpeg, _ffprobe = _require_ffmpeg()
    colors = ["#0f172a", "#1e1b4b", "#064e3b", "#3b0764", "#450a0a", "#172554"]
    bg = colors[shot_index % len(colors)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={bg}:s={width}x{height}:d={duration_s}:r=24",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=stereo:d={duration_s}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ],
        check=True,
    )
    return output_path


def compose_video_with_subtitles(
    clip_paths: list[Path],
    subtitles: list[str],
    output_path: Path,
    *,
    burn_subtitles: bool = True,
    target_width: int = 1280,
    target_height: int = 720,
) -> tuple[Path, Optional[Path]]:
    """Stitches multiple video clips into one MP4 and optionally burns styled Chinese subtitles."""
    if not clip_paths:
        raise ValueError("至少需要 1 个视频片段才能合成。")
    ffmpeg, _ffprobe = _require_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cursor = 0.0
    srt_segments: list[tuple[float, float, str]] = []
    for i, clip in enumerate(clip_paths):
        dur = probe_duration(clip)
        sub_text = subtitles[i] if i < len(subtitles) else ""
        if sub_text.strip():
            srt_segments.append((cursor + 0.15, max(cursor + 0.5, cursor + dur - 0.15), sub_text.strip()))
        cursor += dur

    srt_path: Optional[Path] = None
    if srt_segments:
        srt_path = output_path.with_suffix(".srt")
        write_srt_file(srt_segments, srt_path)

    with tempfile.TemporaryDirectory(prefix="colab-studio-subs-") as tmpdir:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        filter_parts: list[str] = []
        concat_inputs: list[str] = []

        for idx, clip in enumerate(clip_paths):
            cmd.extend(["-i", str(clip)])
            filter_parts.append(
                f"[{idx}:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{idx}]"
            )
            concat_inputs.append(f"[v{idx}]")

        cur_label = "vcat"
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(clip_paths)}:v=1:a=0[{cur_label}]")

        if burn_subtitles and srt_segments:
            base_idx = len(clip_paths)
            for s_idx, (t_start, t_end, text) in enumerate(srt_segments):
                png_path = Path(tmpdir) / f"sub_{s_idx:03d}.png"
                _render_subtitle_png(text, png_path, width=target_width, height=target_height)
                cmd.extend(["-i", str(png_path)])
                next_label = f"vsub{s_idx}"
                filter_parts.append(
                    f"[{cur_label}][{base_idx + s_idx}:v]overlay=0:0:enable='between(t,{t_start:.3f},{t_end:.3f})'[{next_label}]"
                )
                cur_label = next_label

        cmd.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[{cur_label}]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "19",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        subprocess.run(cmd, check=True)
    return output_path, srt_path
