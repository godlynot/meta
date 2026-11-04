"""Web app: teach a procedure with one video, then check attempts against it."""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core import Engine, VideoError, duration, normalize

HERE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", HERE.parent / "data")) / "procedures"
DEMO = HERE.parent / "demo" / "procedures"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "300"))
MAX_VIDEO_S = int(os.environ.get("MAX_VIDEO_S", "180"))
JOBS_PER_HOUR_PER_IP = int(os.environ.get("JOBS_PER_HOUR_PER_IP", "8"))
JOBS_PER_DAY = int(os.environ.get("JOBS_PER_DAY", "80"))
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
log = logging.getLogger("watchonce")

# Fonts are the only third-party resource the page loads.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

DATA.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Watch Once")
app.mount("/media/u", StaticFiles(directory=DATA), name="user-media")
if DEMO.exists():
    app.mount("/media/d", StaticFiles(directory=DEMO), name="demo-media")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response

engine = None
jobs = {}
running = set()  # keep task references alive
recent_by_ip = defaultdict(deque)
daily = {"day": None, "count": 0}


def get_engine():
    global engine
    if engine is None:
        key = os.environ.get("META_API_KEY")
        if not key:
            raise HTTPException(503, "Server is missing META_API_KEY")
        engine = Engine(key)
    return engine


def check_id(value):
    if not ID.fullmatch(value):
        raise HTTPException(404)


def find(pid):
    """Procedures live in DATA (uploaded) or DEMO (bundled with the repo)."""
    check_id(pid)
    for root, base in ((DATA, "/media/u"), (DEMO, "/media/d")):
        if (root / pid / "procedure.json").exists():
            return root / pid, f"{base}/{pid}"
    raise HTTPException(404, "Unknown procedure")


def allow(request):
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    now, today = time.time(), time.strftime("%Y-%m-%d")
    if daily["day"] != today:
        daily.update(day=today, count=0)
    hits = recent_by_ip[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= JOBS_PER_HOUR_PER_IP:
        raise HTTPException(429, "Too many videos from you this hour. Try the samples, or come back later.")
    if daily["count"] >= JOBS_PER_DAY:
        raise HTTPException(429, "The demo hit today's limit. Try the samples instead.")
    hits.append(now)
    daily["count"] += 1


async def save_upload(file: UploadFile, dst: Path, folder: Path):
    """Stream the upload to disk; on any failure remove the half-made folder."""
    limit, size = MAX_UPLOAD_MB * 1024 * 1024, 0
    try:
        with open(dst, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Video is over {MAX_UPLOAD_MB} MB")
                out.write(chunk)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def too_long_message():
    if MAX_VIDEO_S % 60:
        return f"Video is longer than {MAX_VIDEO_S} seconds"
    return f"Video is longer than {MAX_VIDEO_S // 60} minutes"


async def prepare(raw: Path, dst: Path):
    try:
        await asyncio.to_thread(normalize, raw, dst)
    except VideoError as e:
        log.warning("could not convert upload %s: %s", raw, e)
        raise VideoError("That file couldn't be read as a video. Try an MP4 or MOV straight from your phone.")
    finally:
        raw.unlink(missing_ok=True)
    if duration(dst) > MAX_VIDEO_S:
        raise VideoError(too_long_message())


def start_job(work, **ids):
    jid = secrets.token_urlsafe(6)
    jobs[jid] = {"state": "running", "stage": "Uploading", **ids}

    def progress(stage):
        jobs[jid]["stage"] = stage

    async def run():
        try:
            await work(progress)
            jobs[jid]["state"] = "done"
        except (VideoError, HTTPException) as e:
            jobs[jid].update(state="error", error=getattr(e, "detail", None) or str(e))
        except Exception:  # surface API failures to the page instead of hanging
            log.exception("job %s failed", jid)
            jobs[jid].update(state="error", error="Analysis failed. Please try again in a minute.")

    task = asyncio.create_task(run())
    running.add(task)
    task.add_done_callback(running.discard)
    return jid


@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/samples")
async def samples():
    out = []
    if DEMO.exists():
        for d in sorted(DEMO.iterdir()):
            p = d / "procedure.json"
            if p.exists():
                proc = json.loads(p.read_text())
                attempts = sorted(a.name for a in (d / "attempts").iterdir()) if (d / "attempts").exists() else []
                out.append({"id": d.name, "task": proc["task"], "steps": len(proc["steps"]), "attempts": attempts})
    return out


@app.post("/api/procedures")
async def create_procedure(request: Request, file: UploadFile):
    eng = get_engine()
    allow(request)
    pid = secrets.token_urlsafe(6)
    folder = DATA / pid
    folder.mkdir(parents=True)
    raw = folder / "upload.bin"
    await save_upload(file, raw, folder)

    async def work(progress):
        try:
            progress("Converting video")
            await prepare(raw, folder / "expert.mp4")
            procedure = await eng.learn(folder / "expert.mp4", folder, progress)
            (folder / "procedure.json").write_text(json.dumps(procedure, indent=2))
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    return {"job": start_job(work, procedure_id=pid)}


@app.post("/api/procedures/{pid}/attempts")
async def create_attempt(pid: str, request: Request, file: UploadFile):
    eng = get_engine()
    folder, _ = find(pid)
    if folder.parent != DATA:
        # Attempts on a bundled sample are stored with the user's uploads.
        folder = DATA / pid
        if not folder.exists():
            shutil.copytree(DEMO / pid, folder, ignore=shutil.ignore_patterns("attempts"))
    allow(request)
    procedure = json.loads((folder / "procedure.json").read_text())
    aid = secrets.token_urlsafe(6)
    attempt = folder / "attempts" / aid
    attempt.mkdir(parents=True)
    raw = attempt / "upload.bin"
    await save_upload(file, raw, attempt)

    async def work(progress):
        try:
            progress("Converting video")
            await prepare(raw, attempt / "attempt.mp4")
            report = await eng.check(procedure, attempt / "attempt.mp4", attempt, progress)
            (attempt / "report.json").write_text(json.dumps(report, indent=2))
        except Exception:
            shutil.rmtree(attempt, ignore_errors=True)
            raise

    return {"job": start_job(work, procedure_id=pid, attempt_id=aid)}


@app.get("/api/jobs/{jid}")
async def job(jid: str):
    check_id(jid)
    if jid not in jobs:
        raise HTTPException(404, "Unknown job (the server may have restarted)")
    return jobs[jid]


@app.get("/api/procedures/{pid}")
async def get_procedure(pid: str):
    folder, media = find(pid)
    return {**json.loads((folder / "procedure.json").read_text()), "id": pid, "media": media}


@app.get("/api/procedures/{pid}/attempts/{aid}")
async def get_attempt(pid: str, aid: str):
    check_id(pid)
    check_id(aid)
    for root, base in ((DATA, "/media/u"), (DEMO, "/media/d")):
        report = root / pid / "attempts" / aid / "report.json"
        if report.exists():
            data = json.loads(report.read_text())
            data.pop("runs", None)
            return {**data, "id": aid, "media": f"{base}/{pid}/attempts/{aid}"}
    raise HTTPException(404, "Unknown attempt")
