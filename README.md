# Watch Once

**Turn one video of a hands-on task into a coach that checks everyone's attempts.**

Give Watch Once a single video of a task done right: your best technician on camera, or a how-to
tutorial, narrated or silent. It learns the steps. Then anyone can upload a video of their own
attempt, or switch on their camera and work while it watches, and it tells them which steps they
skipped, did out of order or got wrong, with visual evidence and the expert's clip of how it's done.

Built for the Meta Global AI Developer Hackathon 2026 (indie track).
Everything in this repository was written during the hackathon (Oct 29 to Nov 4, 2026).

## Why

Hands-on work is still taught by someone standing over your shoulder: new hires on an assembly
line, technicians on a repair, anyone following a tutorial at home. Existing tools either turn a
video into written instructions and stop there, or need a camera system set up per workstation.
Watch Once needs nothing but one reference video, and it works for any procedure.

## What it does

| | |
|---|---|
| **Learn** | Muse Spark watches the reference video and extracts the ordered steps, the objects involved and which steps are critical. A second pass reviews its own draft against the video. SAM 3.1 outlines the key object in a keyframe for each step. |
| **Check an attempt** | Three independent Muse Spark checks look at every step (done, skipped, out of order, done wrong). A verdict is shown only when most checks agree; disagreement shows as "unclear" instead of a guess. Every mistake gets a frame from the attempt with SAM 3.1's outline, the expert's clip of that step, and a one-line fix. |
| **Replay with coach** | Plays the attempt back and stops to speak up at the moment each mistake would have been noticed. |
| **Coach me live** | The camera watches while you work. A new 8-second clip goes to Muse Spark every 4 seconds; it only reports what it saw, and plain code decides what is a mistake: a later step confirmed by two overlapping clips while an earlier one was never seen, or steps never done. Live alerts cover missing and out-of-order steps only; how well each step was done is judged by the full three-check review that runs automatically when you press Stop. Alerts appear on screen and are spoken. |

## Results on real footage

Reference: one 110-second phone video of making a Nutella sandwich. Learned 8 steps.
Attempt: a separately filmed run that leaves the bread bag open, cuts the slices before
assembling them and leaves the jar open.

| Check | Bag left open | Cut before assembling | Jar left open | False alarms |
|---|---|---|---|---|
| Upload check (3 independent checks) | caught, 3/3 agree | caught, 3/3 and 2/3 | caught, 3/3 | none |
| Same, with a **silent, edited tutorial** as the reference | caught, 3/3 | caught, 3/3 | caught, 3/3 | none |
| Live coaching, final settings, 4 runs | 4/4 | 1/4 live; the review after Stop caught it | 4/4 | none |

Robustness (tests/robustness.js and correct-attempt checks): a correct real run played into live
mode as the webcam produces no alerts; correct attempts score 8/8, 4/4 and 5/5 on the Nutella, tray
and RAM samples with every check agreeing; an idle camera or an immediate Stop stays quiet; videos
with no task, no picture or a few frames are rejected with a plain message.

On a generated 12-step panel assembly with look-alike parts and distractor moves, the upload check
caught every planted mistake (two swapped steps, two parts in the wrong slots, a skipped step) with
3/3 agreement and no false alarms; learning finds 11 to 12 of the 12 steps.

Measured limits: Muse Spark samples about one frame per second of video, so clips are slowed down
before analysis (3x for short videos, less for long ones; up to 15 minutes). Live alerts arrive
about 6 to 10 seconds after a mistake. The smallest movements in a scene (a thin knob line turning)
can still be missed.

## How it uses Meta's models

- **Muse Spark 1.3** (Meta Model API): video understanding for learning, checking and live
  observation, with JSON-schema structured output. Reasoning effort is tuned per job: default for
  the thorough checks, `minimal` for live clips (measured: same observations, half the delay).
- **SAM 3.1** (Meta Model API): text-prompted segmentation of the objects named in each step,
  drawn as the cyan outline on every keyframe and every piece of evidence.

The design rule throughout: the model perceives, code decides. Majority voting across independent
checks, two-clip confirmation in live mode, and ordering logic in plain Python keep verdicts
consistent and checkable.

## Try it

Open the hosted app: it loads with the Nutella sample and a checked attempt. Upload your own
reference and attempt, or press **Coach me live** on a phone (camera access needs the https link).

Run it yourself:

```bash
pip install -r requirements.txt     # needs ffmpeg on PATH
export META_API_KEY=...
uvicorn watchonce.app:app --port 8000
```

Or deploy the `Dockerfile` (Render config in `render.yaml`).

## Code

| Path | What |
|---|---|
| `watchonce/core.py` | Learning, three-check voting, SAM evidence, video preparation |
| `watchonce/live.py` | Live coaching: per-clip observation and the coach's ordering logic |
| `watchonce/app.py` | FastAPI app, uploads, jobs, live sessions, limits and security headers |
| `watchonce/static/` | The page (no framework) |
| `tests/` | End-to-end suites: API (66 checks), browser (29), live with a fake webcam (14), robustness scenarios (what a judge might try), plus generators for the test videos |
| `demo/` | Bundled samples |

Security: uploads are opened by ffmpeg only as real video containers from a local file, every ID
is validated, model output is schema-checked and escaped, responses carry a strict CSP, and the
container runs as a non-root user. Uploads are size-, length- and rate-limited because the demo
runs on one API key.
