"""Render a simple 'pack the tray' procedure: blocks move into a tray, then a lid closes."""
import subprocess, sys
from PIL import Image, ImageDraw
W, H, FPS = 1280, 720, 15
TRAY = (440, 380, 840, 620)
SLOTS = {"red": (470, 470), "blue": (590, 470), "green": (710, 470)}
START = {"red": (80, 120), "blue": (80, 300), "green": (80, 480)}
COLORS = {"red": (220, 40, 40), "blue": (40, 90, 220), "green": (30, 160, 70)}

def render(order, out):
    pos = dict(START); lid_x = 1300
    frames = []
    def snap(n):
        for _ in range(n):
            im = Image.new("RGB", (W, H), (235, 230, 220)); d = ImageDraw.Draw(im)
            d.rectangle(TRAY, outline=(90, 60, 30), width=10)
            for c, (x, y) in pos.items():
                d.rectangle((x, y, x + 100, y + 100), fill=COLORS[c])
            d.rectangle((lid_x, 360, lid_x + 420, 400), fill=(120, 120, 120))
            frames.append(im)
    snap(FPS)
    for item in order:
        if item == "lid":
            for i in range(1, 2 * FPS + 1):
                lid_x = int(1300 + (430 - 1300) * i / (2 * FPS)); snap(1)
        else:
            (x0, y0), (x1, y1) = pos[item], SLOTS[item]
            for i in range(1, 2 * FPS + 1):
                t = i / (2 * FPS); pos[item] = (int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t)); snap(1)
        snap(FPS)
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(f.tobytes())
    p.stdin.close(); p.wait()

render(["red", "blue", "green", "lid"], sys.argv[1] + "/expert.mp4")
render(["red", "lid", "green"], sys.argv[1] + "/trainee.mp4")  # blue skipped, lid before green
