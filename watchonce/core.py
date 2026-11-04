"""Learn a procedure from one expert video, then check attempts against it.

Muse Spark watches the videos, SAM 3.1 outlines the objects involved so every
verdict comes with visual evidence. Each attempt is checked several times
independently and only verdicts a majority agrees on are reported.
"""

import asyncio
import base64
import json
import statistics
import subprocess
from collections import Counter
from pathlib import Path

import meta_sam_parser as sam
from openai import AsyncOpenAI
from PIL import Image, ImageDraw

MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
# Muse Spark samples roughly one frame per second of video time; slowing clips
# down 3x lets it see about six moments per real second (measured). Long videos
# get less slow-down so each pass stays near ~4 minutes of video time.
SLOW = 3


def slow_for(seconds):
    if seconds <= 4 * 60:
        return 3
    if seconds <= 8 * 60:
        return 2
    return 1
VOTES = 3
# Standard-mode prices, USD per 1M tokens (dev.meta.ai/docs/pricing-rate-limits).
PRICE_IN, PRICE_OUT = 1.25, 4.25
SAM_PRICE_PER_IMAGE = 0.0025

STATUSES = ["done", "skipped", "out_of_order", "incorrect", "uncertain"]

PROCEDURE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task", "steps"],
    "properties": {
        "task": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "detail", "start_s", "end_s", "objects", "critical"],
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "objects": {"type": "array", "items": {"type": "string"}},
                    "critical": {"type": "boolean"},
                },
            },
        },
    },
}

AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps", "extra_actions"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "status", "start_s", "end_s", "evidence", "fix"],
                "properties": {
                    "id": {"type": "integer"},
                    "status": {"type": "string", "enum": STATUSES},
                    "start_s": {"type": ["number", "null"]},
                    "end_s": {"type": ["number", "null"]},
                    "evidence": {"type": "string"},
                    "fix": {"type": "string"},
                },
            },
        },
        "extra_actions": {"type": "array", "items": {"type": "string"}},
    },
}

EXTRACT_PROMPT = """You are watching a video of a hands-on procedure done correctly. It may be a raw
recording of an expert or an edited tutorial, with or without narration or on-screen text.
Ignore intros, outros, talking-head segments, sponsor messages and recaps; across jump cuts,
treat the procedure as one continuous sequence.
Break the procedure into its distinct, ordered steps. A step is a unit of work whose outcome
matters to the result and stays visible: a part placed, a cable unplugged, a lid closed, a
module pressed until it clicks. Incidental handling is never a step: picking up or putting
down a tool, moving hands, or the sub-motions of one action (placing a lid and screwing it
tight is one step, "Close the jar").
For each step give the time range in seconds where it happens, the physical objects involved
(short noun phrases a segmentation model could find, most important first, e.g. "RAM stick",
"slot latch"), and whether it is critical. Critical means skipping or botching it causes
damage, injury or a failed result; most steps in a procedure are not critical.
Use narration and on-screen text to understand intent, but only include steps you can see
being performed.
Write titles as short imperatives ("Close both jars")."""

REVIEW_PROMPT = """Below is a first draft of the steps in this procedure video. Watch the whole
video again and return the complete, corrected list:
- add any step the draft missed, including small ones such as sliding, turning, flipping,
  pressing or attaching something, as long as it changes the result
- merge draft steps that are really parts of one action (placing a lid and screwing it
  tight is one step)
- remove anything that is not a step: picking up or putting down a tool, picking
  something up and putting it straight back, moving hands, idle movement
- fix the time ranges, and renumber the steps 1, 2, 3... in the order they happen
Keep the same field rules as the draft.

Draft:
{draft}"""

AUDIT_PROMPT = """You are checking a trainee's attempt at a procedure against the expert's
reference steps below. Watch the trainee video and, for every reference step, decide:
- done: performed correctly and in order
- skipped: never performed
- out_of_order: performed, but before a step that should precede it
- incorrect: attempted but done wrongly (wrong part, wrong place, not completed)
- uncertain: cannot tell from the footage (say why in evidence)
Give the time range in the trainee video where you saw it (null if skipped), the visual
evidence for your decision, and a one-sentence fix for anything not "done" (empty if done).
Also list any actions the trainee took that are not in the reference procedure.
Be strict: only mark "done" if you actually see it happen. The trainee's setup may differ from
the reference (another brand or model of the same thing, a different angle or lighting): judge
whether each step's outcome was achieved, not whether it looks identical.

Reference procedure:
{procedure}"""


class VideoError(Exception):
    pass


def _ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode:
        raise VideoError(r.stderr.strip()[-500:] or "ffmpeg failed")


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise VideoError("not a readable video file")


def has_audio(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


# Uploads are untrusted: only let ffmpeg open them as a real video container from a
# local file, so playlist-style formats (HLS, concat, ...) can never pull in other files.
SAFE_INPUT = ["-protocol_whitelist", "file", "-format_whitelist", "mov,mp4,matroska,webm,avi"]


def normalize(src, dst):
    """Any phone video (.mov, HEVC, rotated) -> 720p H.264 MP4 that browsers play."""
    _ffmpeg(*SAFE_INPUT, "-i", str(src), "-vf", "scale=-2:720,fps=30", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(dst))


def slowed(src, dst, slow=SLOW):
    args = ["-i", str(src), "-vf", f"setpts={slow}*PTS", "-r", "10", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"]
    if has_audio(src):
        tempo, chain = 1 / slow, []
        while tempo < 0.5:  # atempo only accepts factors >= 0.5, so chain them
            chain.append("atempo=0.5")
            tempo /= 0.5
        chain.append(f"atempo={tempo:.6f}")
        args += ["-af", ",".join(chain), "-c:a", "aac"]
    _ffmpeg(*args, str(dst))


def frame_at(video, t, dst):
    _ffmpeg("-ss", f"{max(t, 0):.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dst))


def _unslow(items, slow):
    for it in items:
        for k in ("start_s", "end_s"):
            if it.get(k) is not None:
                it[k] = round(it[k] / slow, 1)


def vote(runs, step_ids):
    """Majority verdict per step across independent audit runs.

    A status needs more than half of the runs to be reported; otherwise the step
    is marked uncertain and the disagreement is shown instead of a guess.
    """
    need = len(runs) // 2 + 1
    merged = []
    for sid in step_ids:
        verdicts = [s for run in runs for s in run["steps"] if s["id"] == sid]
        # Model output is untrusted: anything outside the schema counts as no verdict.
        counts = Counter(v["status"] for v in verdicts if v["status"] in STATUSES)
        status, n = counts.most_common(1)[0] if counts else ("uncertain", 0)
        if n < need:
            tally = ", ".join(f"{k} ×{c}" for k, c in counts.most_common())
            merged.append({"id": sid, "status": "uncertain", "start_s": None, "end_s": None,
                           "evidence": f"Independent checks disagreed ({tally}).",
                           "fix": "Re-record this step closer to the camera.",
                           "agreement": f"{n}/{len(runs)}"})
            continue
        agreeing = [v for v in verdicts if v["status"] == status]
        starts = [v["start_s"] for v in agreeing if v["start_s"] is not None]
        ends = [v["end_s"] for v in agreeing if v["end_s"] is not None]
        merged.append({"id": sid, "status": status,
                       "start_s": statistics.median(starts) if starts else None,
                       "end_s": statistics.median(ends) if ends else None,
                       "evidence": agreeing[0]["evidence"], "fix": agreeing[0]["fix"],
                       "agreement": f"{n}/{len(runs)}"})
    return merged


def alert_times(steps, video_len):
    """When a coach watching live would have noticed each mistake.

    Wrong or out-of-order steps are noticed when they happen; a skipped step is
    noticed when the next later step starts (or at the end of the video).
    """
    seen = [s for s in steps if s["start_s"] is not None]
    for s in steps:
        if s["status"] in ("incorrect", "out_of_order"):
            s["alert_s"] = s["start_s"]
        elif s["status"] == "skipped":
            later = [x["start_s"] for x in seen if x["id"] > s["id"]]
            s["alert_s"] = min(later) if later else round(max(video_len - 0.5, 0), 1)
        else:
            s["alert_s"] = None


def settled(step):
    """A moment just before a step ends: the action is finished and the object is in place."""
    end = step["end_s"] if step["end_s"] is not None else step["start_s"]
    return max(step["start_s"], end - 0.3)


def draw_overlay(image_path, records, dst):
    img = Image.open(image_path).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for r in records:
        if r.kind == "mask":
            b, m = r.bounds, r.mask
            raster = sam.decode_mask_to_raster(m)
            mask = Image.frombytes("L", (m.width, m.height), bytes(255 if p else 0 for p in raster))
            fill = Image.new("RGBA", (m.width, m.height), (0, 200, 255, 90))
            layer.paste(fill, (b.left, b.top), mask)
        elif r.kind == "box":
            pad = 12  # frame the object instead of tracing its edge, so it stays visible
            draw.rounded_rectangle((r.left - pad, r.top - pad, r.right + pad, r.bottom + pad),
                                   radius=10, outline=(0, 200, 255, 255), width=6)
    Image.alpha_composite(img, layer).convert("RGB").save(dst, quality=85)


class Engine:
    def __init__(self, api_key):
        self.client = AsyncOpenAI(base_url="https://api.meta.ai/v1", api_key=api_key)
        self.sam_slots = asyncio.Semaphore(6)

    async def _upload(self, path):
        with open(path, "rb") as f:
            return (await self.client.files.create(file=f, purpose="user_data")).id

    async def _ask(self, file_id, prompt, schema, name):
        r = await self.client.responses.create(
            model=MODEL,
            max_output_tokens=32000,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_file", "file_id": file_id},
            ]}],
            text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        )
        cost = r.usage.input_tokens / 1e6 * PRICE_IN + r.usage.output_tokens / 1e6 * PRICE_OUT
        return json.loads(r.output_text), cost

    async def _on_slowed(self, video, workdir):
        """Upload a slowed copy for analysis; returns (file id, slow-down used)."""
        slow = slow_for(duration(video))
        slow_path = Path(workdir) / "analysis.mp4"
        await asyncio.to_thread(slowed, video, slow_path, slow)
        try:
            return await self._upload(slow_path), slow
        finally:
            slow_path.unlink(missing_ok=True)

    async def highlight(self, image_path, phrase, dst):
        """Outline `phrase` in the image with SAM 3.1. Returns False if nothing matched."""
        url = "data:image/jpeg;base64," + base64.b64encode(Path(image_path).read_bytes()).decode()
        async with self.sam_slots:
            events = await self.client.responses.create(
                model=SAM_MODEL, stream=True, metadata={"mask_encoding": "one_bit"},
                input=[{"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": phrase},
                    {"type": "input_image", "image_url": url},
                ]}],
            )
            result = await sam.parse_responses_stream(events, sam.image_segmentation_format()).final_result()
        # Keep the first object only: the one SAM is most sure about.
        first = [r for r in result.records if r.kind in ("box", "mask") and r.object_id == "0"]
        if not first:
            return False
        await asyncio.to_thread(draw_overlay, image_path, first, dst)
        return True

    async def _frame_with_highlight(self, video, t, phrase, frames_dir, name):
        raw = frames_dir / f"{name}_raw.jpg"
        out = frames_dir / f"{name}.jpg"
        await asyncio.to_thread(frame_at, video, t, raw)
        try:
            outlined = phrase and await self.highlight(raw, phrase, out)
        except Exception:
            outlined = False
        if not outlined:
            raw.replace(out)
        else:
            raw.unlink(missing_ok=True)
        return out.name, bool(outlined)

    async def learn(self, video, workdir, progress=lambda stage: None):
        """Expert video -> ordered procedure with a highlighted keyframe per step."""
        workdir = Path(workdir)
        frames = workdir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        progress("Preparing video")
        file_id, slow = await self._on_slowed(video, workdir)
        try:
            progress("Muse Spark is watching the expert")
            draft, cost = await self._ask(file_id, EXTRACT_PROMPT, PROCEDURE_SCHEMA, "procedure")
            # One pass misses small actions on long procedures (measured: 8 of 12 steps
            # on a 12-step assembly), so a second pass reviews the draft against the video.
            progress("Muse Spark is double-checking the steps")
            procedure, review_cost = await self._ask(
                file_id, REVIEW_PROMPT.format(draft=json.dumps(draft, indent=2)), PROCEDURE_SCHEMA, "procedure")
            cost += review_cost
            procedure["draft_steps"] = len(draft["steps"])
        finally:
            await self.client.files.delete(file_id)
        _unslow(procedure["steps"], slow)

        progress("SAM 3.1 is outlining each step")
        shots = await asyncio.gather(*[
            self._frame_with_highlight(video, settled(s),
                                       s["objects"][0] if s["objects"] else "", frames, f"step{s['id']}")
            for s in procedure["steps"]
        ])
        for s, (name, outlined) in zip(procedure["steps"], shots):
            s["frame"], s["outlined"] = f"frames/{name}", outlined
        procedure["cost_usd"] = round(cost + len(shots) * SAM_PRICE_PER_IMAGE, 4)
        procedure["duration_s"] = round(duration(video), 1)
        return procedure

    async def check(self, procedure, video, workdir, progress=lambda stage: None, votes=VOTES):
        """Attempt video -> per-step verdicts agreed by a majority of independent checks."""
        workdir = Path(workdir)
        frames = workdir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        progress("Preparing video")
        file_id, slow = await self._on_slowed(video, workdir)
        reference = [{k: v for k, v in s.items() if k in ("id", "title", "detail", "objects", "critical")}
                     for s in procedure["steps"]]
        prompt = AUDIT_PROMPT.format(procedure=json.dumps(reference, indent=2))
        try:
            progress(f"Running {votes} independent checks")
            answers = await asyncio.gather(*[
                self._ask(file_id, prompt, AUDIT_SCHEMA, "audit") for _ in range(votes)
            ])
        finally:
            await self.client.files.delete(file_id)
        runs = [a for a, _ in answers]
        for run in runs:
            _unslow(run["steps"], slow)

        length = duration(video)
        await asyncio.to_thread(frame_at, video, min(1.0, length / 2), workdir / "poster.jpg")
        steps = vote(runs, [s["id"] for s in procedure["steps"]])
        alert_times(steps, length)
        by_id = {s["id"]: s for s in procedure["steps"]}
        for s in steps:
            s["title"] = by_id[s["id"]]["title"]
            s["critical"] = by_id[s["id"]]["critical"]

        progress("SAM 3.1 is gathering visual evidence")
        issues = [s for s in steps if s["status"] not in ("done", "uncertain")]
        shots = await asyncio.gather(*[
            self._frame_with_highlight(video, settled(s) if s["start_s"] is not None else s["alert_s"],
                                       (by_id[s["id"]]["objects"] or [""])[0], frames, f"issue{s['id']}")
            for s in issues
        ])
        for s, (name, outlined) in zip(issues, shots):
            s["frame"], s["outlined"] = f"frames/{name}", outlined

        # Extra actions: take them from the run that agrees most with the majority.
        majority = {s["id"]: s["status"] for s in steps}
        best = max(runs, key=lambda run: sum(majority.get(v["id"]) == v["status"] for v in run["steps"]))
        return {
            "steps": steps,
            "extra_actions": best["extra_actions"],
            "votes": votes,
            "duration_s": round(length, 1),
            "cost_usd": round(sum(c for _, c in answers) + len(shots) * SAM_PRICE_PER_IMAGE, 4),
            "runs": runs,
        }
