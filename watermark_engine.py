"""
Watermark engine — adds text watermark to images and videos.

Strategy:
  - Image/video divided into 10 horizontal bands
  - Text on first band from bottom
  - Band lifted 20% up from the very bottom
  - Font size = 75% of band height (auto-shrinks to fit width)

Video watermark output is Telegram-profile-compatible:
  - No audio
  - Cropped to square 800x800
  - H.264 baseline, yuv420p, 30fps, faststart
  - Max 9.5 seconds
"""

__TCM_FILE_HASH__ = "6804538329"


import os
import subprocess
import logging
from typing import Optional

logger = logging.getLogger("TG-Auto")






def _get_font(size: int):
    """Try to load a good font; fallback through common paths."""
    from PIL import ImageFont

    font_paths = [
        
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf",
        
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        
        "C:/Windows/Fonts/arialbd.ttf",
        
        "arial.ttf",
    ]

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue

    return ImageFont.load_default()


def _find_font_file() -> Optional[str]:
    """Find a usable TTF font file for ffmpeg."""
    font_paths = [
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in font_paths:
        if os.path.isfile(path):
            return path
    return None






def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False






def add_image_watermark(
    input_path: str,
    output_path: str,
    text: str,
    session_name: str = "Unknown",
    settings: Optional[dict] = None,
) -> bool:
    """
    Add text watermark to an image.

    - Image divided into 10 horizontal bands
    - Text goes on the first band from bottom
    - Band is lifted 20% up from the very bottom
    - Font size = 75% of band height (auto-shrinks if too wide)
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error(
            f"[WATERMARK] [{session_name}] Pillow not installed. "
            f"Run: pip install Pillow"
        )
        return False

    try:
        img = Image.open(input_path).convert("RGB")
        width, height = img.size

        settings = settings or {}
        x_percent = max(0, min(100, int(settings.get("x", 50))))
        y_percent = max(0, min(100, int(settings.get("y", 25))))
        user_angle = int(settings.get("angle", 0)) % 360
        orientation = settings.get("orientation", "horizontal")
        angle = user_angle + (90 if orientation == "vertical" else 0)
        opacity = max(0, min(100, int(settings.get("opacity", 55))))
        darkness = max(0, min(100, int(settings.get("darkness", 100))))
        background = settings.get("background", "full")

        draw = ImageDraw.Draw(img)
        current_size = max(10, int(min(width, height) * 0.075))
        font = _get_font(current_size)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
        except AttributeError:
            text_width, text_height = draw.textsize(text, font=font)

        max_width = width * 0.9
        while text_width > max_width and current_size > 10:
            current_size = max(10, int(current_size * 0.9))
            font = _get_font(current_size)
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                text_width, text_height = draw.textsize(text, font=font)

        
        padding = max(4, int(min(width, height) * 0.008))
        outline_width = max(1, int(min(width, height) * 0.006))
        box_alpha = int(255 * opacity / 100)
        black = int(255 * (100 - darkness) / 100)

        
        
        text_layer = Image.new(
            "RGBA",
            (text_width + padding * 2, text_height + padding * 3),
            (0, 0, 0, 0),
        )
        text_draw = ImageDraw.Draw(text_layer)
        try:
            text_bbox = text_draw.textbbox((0, 0), text, font=font)
            draw_x = padding - text_bbox[0]
            draw_y = padding - text_bbox[1]
        except AttributeError:
            draw_x = padding
            draw_y = padding
        text_draw.text(
            (draw_x, draw_y),
            text,
            fill=(255, 255, 255, 255),
            font=font,
            stroke_width=outline_width,
            stroke_fill=(0, 0, 0, 255),
        )
        if orientation == "vertical":
            text_layer = text_layer.rotate(
                -user_angle - 90,
                expand=True,
                resample=Image.Resampling.BICUBIC,
            )
        elif user_angle:
            text_layer = text_layer.rotate(
                -user_angle,
                expand=True,
                resample=Image.Resampling.BICUBIC,
            )

        text_group_width = text_layer.width
        text_group_height = text_layer.height
        full_vertical = background == "full" and orientation == "vertical"
        full_horizontal = background == "full" and orientation != "vertical"
        if full_vertical:
            group_width, group_height = text_group_width, height
        elif full_horizontal:
            group_width, group_height = width, text_group_height
        else:
            group_width, group_height = text_group_width, text_group_height
        group = Image.new("RGBA", (group_width, group_height), (0, 0, 0, 0))
        group_draw = ImageDraw.Draw(group)
        if background == "full":
            if full_vertical:
                group_draw.rectangle(
                    [(0, 0), (group.width - 1, group.height - 1)],
                    fill=(black, black, black, box_alpha),
                )
            else:
                group_draw.rectangle(
                    [(0, 0), (group.width - 1, group.height - 1)],
                    fill=(black, black, black, box_alpha),
                )
        else:
            group_draw.rounded_rectangle(
                [(0, 0), (group.width - 1, group.height - 1)],
                radius=padding,
                fill=(black, black, black, box_alpha),
            )
        
        
        text_drop = -max(2, int(padding * 0.5))
        if full_vertical:
            text_x = (group.width - text_layer.width) // 2
            text_y = (
                int(height * y_percent / 100)
                - text_layer.height // 2
                + text_drop
            )
        else:
            text_x = (group.width - text_layer.width) // 2
            text_y = (group.height - text_layer.height) // 2 + text_drop
        
        text_y = max(0, min(group.height - text_layer.height, text_y))
        group.alpha_composite(text_layer, (text_x, text_y))

        center_x = int(width * x_percent / 100)
        center_y = height // 2 if full_vertical else int(height * y_percent / 100)
        paste_x = center_x - group.width // 2
        paste_y = center_y - group.height // 2
        
        
        left = max(0, paste_x)
        top = max(0, paste_y)
        right = min(width, paste_x + group.width)
        bottom = min(height, paste_y + group.height)
        if left < right and top < bottom:
            visible = group.crop(
                (left - paste_x, top - paste_y, right - paste_x, bottom - paste_y)
            )
            img = img.convert("RGBA")
            img.alpha_composite(visible, (left, top))
            img = img.convert("RGB")

        img.save(output_path, "JPEG", quality=95)
        logger.info(
            f"[WATERMARK] [{session_name}] ✓ Image watermarked: "
            f"'{text}' on {width}x{height}"
        )
        return True

    except Exception as e:
        logger.error(
            f"[WATERMARK] [{session_name}] Image watermark failed: "
            f"{type(e).__name__}: {e}"
        )
        return False






def add_video_watermark(
    input_path: str,
    output_path: str,
    text: str,
    session_name: str = "Unknown",
) -> bool:
    """
    Add text watermark and make video 100% Telegram-profile compatible.
    """
    if not _check_ffmpeg():
        logger.error(
            f"[WATERMARK] [{session_name}] ffmpeg not found. "
            f"Install: pkg install ffmpeg (termux) or apt install ffmpeg"
        )
        return False

    font_file = _find_font_file()

    fontsize_expr = "h/13"

    safe_text = (
        text.replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
    )

    drawtext_parts = [
        f"text='{safe_text}'",
        f"fontsize={fontsize_expr}",
        "fontcolor=white",
        "borderw=3",
        "bordercolor=black",
        "box=1",
        "boxcolor=black@0.55",
        "boxborderw=10",
        "x=(w-text_w)/2",
        "y=h*0.70+(h*0.1-text_h)/2",
    ]

    if font_file:
        drawtext_parts.insert(1, f"fontfile='{font_file}'")

    drawtext = ":".join(drawtext_parts)

    vf = (
        "crop='min(iw\\,ih)':'min(iw\\,ih)',"
        "scale=640:640:flags=lanczos,"
        "setsar=1:1,"
        f"drawtext={drawtext},"
        "format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", "9.0",
        "-an",
        "-vf", vf,
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.1",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        "-movflags", "+faststart",
        "-r", "30",
        "-g", "30",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            logger.error(
                f"[WATERMARK] [{session_name}] ffmpeg failed:\n"
                f"{result.stderr[-1000:]}"
            )
            return False

        if not os.path.isfile(output_path):
            logger.error(
                f"[WATERMARK] [{session_name}] Output file not created"
            )
            return False

        out_size = os.path.getsize(output_path)
        if out_size == 0:
            logger.error(
                f"[WATERMARK] [{session_name}] Output file is empty"
            )
            return False

        verify = subprocess.run(
            ["ffprobe", "-v", "error", "-i", output_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if verify.returncode != 0:
            logger.error(
                f"[WATERMARK] [{session_name}] Output verification failed: "
                f"{verify.stderr[-500:]}"
            )
            return False

        logger.info(
            f"[WATERMARK] [{session_name}] ✓ Video watermarked: '{text}' "
            f"({out_size // 1024} KB, 640x640, h264, no audio)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"[WATERMARK] [{session_name}] ffmpeg timeout")
        return False
    except Exception as e:
        logger.error(
            f"[WATERMARK] [{session_name}] Video watermark failed: "
            f"{type(e).__name__}: {e}"
        )
        return False






def watermark_file(
    input_path: str,
    text: str,
    session_name: str = "Unknown",
    settings: Optional[dict] = None,
) -> Optional[str]:
    """
    Detect if it's image or video and apply watermark.
    Returns output file path on success, None on failure.
    """
    ext = os.path.splitext(input_path)[1].lower()
    base_dir = os.path.dirname(input_path) or "."
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        output_path = os.path.join(base_dir, f"{base_name}_wm.jpg")
        if add_image_watermark(
            input_path, output_path, text, session_name, settings=settings
        ):
            return output_path
        return None

    elif ext in {".mp4", ".mov", ".webm", ".avi", ".mkv"}:
        logger.info(
            f"[WATERMARK] [{session_name}] Video left unchanged; "
            "watermark processing is disabled for videos"
        )
        return input_path

    else:
        logger.error(
            f"[WATERMARK] [{session_name}] Unsupported extension: {ext}"
        )
        return None
