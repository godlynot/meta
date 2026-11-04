"""Render a harder synthetic procedure: a 12-step control-panel assembly.

Several steps look alike (three red squares into three slots, two blue circles),
a hand carries every part, and the hand sometimes picks a part up and puts it
back (a distractor that is not a step). Writes expert.mp4 and attempt.mp4 plus
the ground truth for the attempt.

usage: python make_complex.py OUT_DIR
"""
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

W, H, FPS = 1280, 720, 10
BOARD = (420, 140, 1200, 640)
SLOTS = {"A1": (470, 190), "A2": (720, 190), "A3": (970, 190), "B1": (470, 420), "B2": (720, 420), "B3": (970, 420)}
TRAY = {"r1": (40, 120), "r2": (40, 240), "r3": (40, 360), "b1": (180, 120), "b2": (180, 240), "y1": (180, 360)}
COLOR = {"r": (205, 45, 45), "b": (45, 95, 210), "y": (230, 185, 30)}
HOME = (300, 640)

EXPERT = [
    ("place", "r1", "A1"), ("place", "r2", "A2"), ("place", "b1", "B1"), ("place", "r3", "A3"),
    ("place", "b2", "B2"), ("place", "y1", "B3"), ("lock", "A"), ("lock", "B"),
    ("knob",), ("label",), ("switch",), ("cover",),
]
STEP_NAMES = [
    "Place red square in top-left slot", "Place red square in top-middle slot",
    "Place blue circle in bottom-left slot", "Place red square in top-right slot",
    "Place blue circle in bottom-middle slot", "Place yellow triangle in bottom-right slot",
    "Slide green lock bar over top row", "Slide green lock bar over bottom row",
    "Turn the knob", "Attach the white label", "Flip the switch", "Slide the cover down",
]
# Attempt: steps 2 and 3 swapped, blue circle in the wrong slot (so the triangle
# lands in the wrong slot too), knob never turned, plus an extra distractor.
ATTEMPT = [
    ("place", "r1", "A1"), ("place", "b1", "B1"), ("place", "r2", "A2"), ("place", "r3", "A3"),
    ("place", "b2", "B3"), ("place", "y1", "B2"), ("lock", "A"), ("lock", "B"),
    ("label",), ("switch",), ("cover",),
]
TRUTH = {"out_of_order": [2, 3], "incorrect": [5, 6], "skipped": [9]}


class Scene:
    def __init__(self):
        self.parts = {k: list(v) for k, v in TRAY.items()}
        self.hand = list(HOME)
        self.locks = {"A": -1200, "B": -1200}  # off the left edge until slid in
        self.knob = 0.0
        self.label = False
        self.switch = 0.0
        self.cover = -520
        self.frames = []

    def draw(self):
        im = Image.new("RGB", (W, H), (232, 226, 214))
        d = ImageDraw.Draw(im)
        d.rectangle(BOARD, fill=(70, 78, 86))
        for x, y in SLOTS.values():
            d.rectangle((x, y, x + 120, y + 120), outline=(160, 170, 175), width=4)
        for name, (x, y) in self.parts.items():
            c = COLOR[name[0]]
            if name[0] == "r":
                d.rectangle((x + 10, y + 10, x + 110, y + 110), fill=c)
            elif name[0] == "b":
                d.ellipse((x + 10, y + 10, x + 110, y + 110), fill=c)
            else:
                d.polygon([(x + 60, y + 8), (x + 112, y + 110), (x + 8, y + 110)], fill=c)
        for row, y in (("A", 172), ("B", 402)):
            x = BOARD[0] + 20 + self.locks[row]
            if x + 720 > 0:
                d.rectangle((x, y, x + 720, y + 16), fill=(40, 170, 90))
        cx, cy = 1140, 600  # knob
        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), fill=(200, 200, 200))
        a = math.radians(self.knob)
        d.line((cx, cy, cx + 24 * math.cos(a), cy + 24 * math.sin(a)), fill=(20, 20, 20), width=5)
        if self.label:
            d.rectangle((600, 600, 840, 632), fill=(250, 250, 250))
            d.text((612, 608), "PANEL 7", fill=(30, 30, 30))
        sx = 460 + int(self.switch * 40)  # switch
        d.rectangle((450, 596, 520, 630), outline=(230, 230, 230), width=3)
        d.rectangle((sx, 600, sx + 26, 626), fill=(230, 80, 40))
        if self.cover > -520:
            top = BOARD[1] + self.cover
            d.rectangle((BOARD[0], BOARD[1], BOARD[2], max(BOARD[1], top + 500)), fill=(150, 150, 150))
        hx, hy = self.hand
        d.ellipse((hx - 34, hy - 34, hx + 34, hy + 34), fill=(222, 184, 150))
        self.frames.append(im)

    def hold(self, seconds):
        for _ in range(int(seconds * FPS)):
            self.draw()

    def move_hand(self, to, seconds=1.2, carry=None):
        x0, y0 = self.hand
        n = int(seconds * FPS)
        for i in range(1, n + 1):
            t = i / n
            self.hand = [x0 + (to[0] - x0) * t, y0 + (to[1] - y0) * t]
            if carry:
                self.parts[carry] = [self.hand[0] - 60, self.hand[1] - 60]
            self.draw()

    def tween(self, attr, key, end, seconds=1.5):
        start = getattr(self, attr)[key] if key is not None else getattr(self, attr)
        n = int(seconds * FPS)
        for i in range(1, n + 1):
            v = start + (end - start) * i / n
            if key is not None:
                getattr(self, attr)[key] = v
            else:
                setattr(self, attr, v)
            self.draw()

    def do(self, action):
        kind = action[0]
        if kind == "place":
            _, part, slot = action
            px, py = self.parts[part]
            self.move_hand((px + 60, py + 60))
            sx, sy = SLOTS[slot]
            self.move_hand((sx + 60, sy + 60), 1.6, carry=part)
        elif kind == "lock":
            row = action[1]
            self.move_hand((BOARD[0] + 40, 180 if row == "A" else 410))
            self.tween("locks", row, 0)
        elif kind == "knob":
            self.move_hand((1140, 600))
            self.tween("knob", None, 180.0, 2.0)
        elif kind == "label":
            self.move_hand((720, 616))
            self.label = True
            self.hold(0.8)
        elif kind == "switch":
            self.move_hand((485, 613))
            self.tween("switch", None, 1.0, 0.8)
        elif kind == "cover":
            self.move_hand((800, 150))
            self.tween("cover", None, 0, 2.0)
        self.move_hand(HOME, 0.8)
        self.hold(2.5)

    def distractor(self, part):
        """Pick a part up and put it straight back: not a step."""
        px, py = self.parts[part]
        self.move_hand((px + 60, py + 60))
        self.move_hand((px + 160, py + 40), 0.8, carry=part)
        self.move_hand((px + 60, py + 60), 0.8, carry=part)
        self.move_hand(HOME, 0.8)
        self.hold(1.5)


def render(actions, out, distract_after):
    s = Scene()
    s.hold(2)
    for i, a in enumerate(actions):
        s.do(a)
        if i in distract_after:
            remaining = [p for p in TRAY if list(TRAY[p]) == s.parts[p]]
            if remaining:
                s.distractor(remaining[0])
    s.hold(2)
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          str(out)], stdin=subprocess.PIPE)
    for f in s.frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    return len(s.frames) / FPS


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    print("expert", render(EXPERT, out / "expert.mp4", distract_after={1, 4}), "s")
    print("attempt", render(ATTEMPT, out / "attempt.mp4", distract_after={0, 3, 6}), "s")
    (out / "truth.json").write_text(json.dumps({"steps": STEP_NAMES, "attempt_mistakes": TRUTH}, indent=2))
