"""Render the 'pack the tray' sample videos: smooth, anti-aliased, 30 fps.

usage: python scripts/make_tray_demo.py OUT_DIR
Writes expert.mp4 (red, blue, green into the tray, then the lid) and
attempt.mp4 (blue skipped, lid closed before green).
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H, FPS, SS = 1280, 720, 30, 2          # output size, frame rate, supersampling
MOVE, PAUSE = 0.9, 0.5                     # seconds per motion and between steps
BG, TRAY_C, LID_C = (237, 234, 228), (94, 64, 38), (128, 132, 136)
TRAY = (440, 360, 840, 600)
COLORS = {"red": (214, 54, 48), "blue": (46, 102, 214), "green": (36, 158, 86)}
START = {"red": (90, 110), "blue": (90, 290), "green": (90, 470)}
SLOTS = {"red": (468, 440), "blue": (588, 440), "green": (708, 440)}
LID_OPEN, LID_SHUT = 1320, 430


def ease(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def frame(pos, lid_x):
    s = SS
    im = Image.new("RGB", (W * s, H * s), BG)
    shadow = Image.new("L", im.size, 0)
    sd = ImageDraw.Draw(shadow)
    for x, y in pos.values():
        sd.rounded_rectangle(((x + 6) * s, (y + 10) * s, (x + 106) * s, (y + 110) * s), radius=14 * s, fill=70)
    sd.rounded_rectangle(((lid_x + 6) * s, 348 * s, (lid_x + 426) * s, 392 * s), radius=10 * s, fill=60)
    shadow = shadow.filter(ImageFilter.GaussianBlur(9 * s))
    im.paste((0, 0, 0), mask=shadow.point(lambda v: v // 3))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = TRAY
    d.rounded_rectangle((x0 * s, y0 * s, x1 * s, y1 * s), radius=18 * s, outline=TRAY_C, width=12 * s)
    for name, (x, y) in pos.items():
        d.rounded_rectangle((x * s, y * s, (x + 100) * s, (y + 100) * s), radius=14 * s, fill=COLORS[name])
    d.rounded_rectangle((lid_x * s, 340 * s, (lid_x + 420) * s, 380 * s), radius=10 * s, fill=LID_C)
    return im.resize((W, H), Image.LANCZOS)


def render(order, out):
    pos, lid_x, frames = dict(START), LID_OPEN, []

    def hold(sec):
        frames.extend([frame(pos, lid_x)] * int(sec * FPS))

    hold(0.6)
    for item in order:
        n = int(MOVE * FPS)
        if item == "lid":
            for i in range(1, n + 1):
                lid_x = LID_OPEN + (LID_SHUT - LID_OPEN) * ease(i / n)
                frames.append(frame(pos, lid_x))
        else:
            (ax, ay), (bx, by) = pos[item], SLOTS[item]
            for i in range(1, n + 1):
                t = ease(i / n)
                pos[item] = (ax + (bx - ax) * t, ay + (by - ay) * t)
                frames.append(frame(pos, lid_x))
        hold(PAUSE)
    hold(1.0)
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20",
                          "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    return len(frames) / FPS


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    print("expert", render(["red", "blue", "green", "lid"], out / "expert.mp4"), "s")
    print("attempt", render(["red", "lid", "green"], out / "attempt.mp4"), "s")
