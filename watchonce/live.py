"""Live coaching: watch a trainee through the camera a few seconds at a time.

Muse Spark only reports what it sees in each short clip (which reference steps
were performed, and whether correctly). Deciding what is a mistake - a step
skipped, done out of order, or left undone at the end - is plain code over that
running record, so the coach's alerts are consistent and checkable.
"""

import asyncio
import base64
import json
from pathlib import Path

from .core import MODEL, PRICE_IN, PRICE_OUT, SAFE_INPUT, _ffmpeg

# Live clips are sent at normal speed: slowing them down doubles what the model
# has to watch and the delay before an alert. Clips overlap instead (a new
# 8-second clip every 4 seconds), so most moments are seen twice.

WRONG_VOTES = 2

OBSERVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["performed", "working_on"],
    "properties": {
        "performed": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "correct", "note"],
                "properties": {
                    "id": {"type": "integer"},
                    "correct": {"type": "boolean"},
                    "note": {"type": "string"},
                },
            },
        },
        "working_on": {"type": ["integer", "null"]},
    },
}

OBSERVE_PROMPT = """You are watching a few seconds of live camera footage of a trainee doing
a hands-on procedure. The reference steps are below. The trainee's setup may differ from the
reference (another model of the same thing, a different angle); judge the outcome of each step.
Steps already seen completed earlier in the session: {done}.

List every reference step you SEE being performed or completed in this clip, in the
order it happens. A step that only fetches a tool counts as done if the tool is already
in the trainee's hand. For every other step, only count it if you see the action itself.
For each, say whether it was done correctly; if not, say what was wrong in a short note
written to the trainee, describing only what you saw. Report only what is in
this clip; do not repeat steps from earlier unless they are clearly done again. Also give the id of the step the trainee seems
to be working on at the end of the clip, or null if you can't tell.

Reference procedure:
{procedure}"""


class LiveCoach:
    """Turns per-clip observations into coaching alerts. Pure logic, no I/O."""

    def __init__(self, steps):
        self.steps = sorted(steps, key=lambda s: s["id"])
        self.titles = {s["id"]: s["title"] for s in self.steps}
        self.critical = {s["id"]: s.get("critical", False) for s in self.steps}
        self.done = []          # step ids in the order they were completed
        self.flagged = set()    # earlier steps we already warned were skipped
        self.wrong = set()      # steps we already warned were done wrongly
        self.wrong_votes = {}   # step id -> how many clips reported it done wrongly
        self.working_on = None

    def _alert(self, kind, sid, message):
        return {"kind": kind, "id": sid, "title": self.titles[sid],
                "critical": self.critical[sid], "message": message}

    def observe(self, performed, working_on=None):
        alerts = []
        for p in performed:
            sid = p.get("id")
            if sid not in self.titles:
                continue  # the model named a step that isn't in the procedure
            if not p.get("correct", True) and sid not in self.wrong:
                # Clips overlap, so every moment is seen twice: only call a step wrong
                # when two clips agree. One clip's misreading stays quiet.
                self.wrong_votes[sid] = self.wrong_votes.get(sid, 0) + 1
                if self.wrong_votes[sid] >= WRONG_VOTES:
                    self.wrong.add(sid)
                    alerts.append(self._alert("incorrect", sid, p.get("note") or f"Step {sid} isn't right."))
            if sid in self.done:
                continue
            missing = [s["id"] for s in self.steps
                       if s["id"] < sid and s["id"] not in self.done and s["id"] not in self.flagged]
            for m in missing:
                self.flagged.add(m)
                alerts.append(self._alert("skipped", m, f"Step {m} isn't done yet: {self.titles[m]}."))
            self.done.append(sid)
        if working_on in self.titles:
            self.working_on = working_on
        return alerts

    def finish(self):
        """At the end, every step never done is a miss (unless already flagged)."""
        alerts = [self._alert("skipped", s["id"], f"Step {s['id']} was never done: {s['title']}.")
                  for s in self.steps if s["id"] not in self.done and s["id"] not in self.flagged]
        self.flagged.update(a["id"] for a in alerts)
        return alerts

    def state(self):
        status = {}
        for s in self.steps:
            sid = s["id"]
            if sid in self.wrong:
                status[sid] = "incorrect"
            elif sid in self.done:
                late = sid in self.flagged
                status[sid] = "out_of_order" if late else "done"
            elif sid in self.flagged:
                status[sid] = "skipped"
            else:
                status[sid] = "pending"
        return {"status": status, "working_on": self.working_on, "done": len(self.done), "total": len(self.steps)}


def is_small_mp4(path, limit=10 * 1024 * 1024):
    """Phones and Chrome record MP4 already; those clips can go to Muse Spark as they
    are, which skips a re-encode that took most of the time on a small server."""
    p = Path(path)
    with open(p, "rb") as f:
        return f.read(12)[4:8] == b"ftyp" and p.stat().st_size < limit


def prepare_clip(src, dst):
    """Browser recording (WebM or MP4) -> small MP4 in one pass. The clip is
    untrusted, so it is opened with the same container whitelist as uploads."""
    _ffmpeg(*SAFE_INPUT, "-i", str(src), "-an", "-vf", "scale=-2:480", "-r", "10", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", str(dst))


async def look(engine, procedure, done, clip, workdir):
    """One clip -> what Muse Spark saw in it. Doesn't touch the coach, so several
    clips can be looked at in parallel and applied in order afterwards."""
    analysis = Path(workdir) / f"{Path(clip).stem}_live.mp4"
    if is_small_mp4(clip):
        analysis = Path(clip)
    else:
        await asyncio.to_thread(prepare_clip, clip, analysis)
    url = "data:video/mp4;base64," + base64.b64encode(analysis.read_bytes()).decode()
    reference = [{k: v for k, v in s.items() if k in ("id", "title", "detail", "objects")}
                 for s in procedure["steps"]]
    prompt = OBSERVE_PROMPT.format(done=done or "none", procedure=json.dumps(reference, indent=2))
    r = await engine.client.responses.create(
        model=MODEL,
        max_output_tokens=8000,
        reasoning={"effort": "minimal"},  # measured: same observations, about half the delay of "low"
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_video", "video_url": url},
        ]}],
        text={"format": {"type": "json_schema", "name": "observation", "strict": True,
                         "schema": OBSERVE_SCHEMA}},
    )
    seen = json.loads(r.output_text)
    cost = r.usage.input_tokens / 1e6 * PRICE_IN + r.usage.output_tokens / 1e6 * PRICE_OUT
    return seen, cost
