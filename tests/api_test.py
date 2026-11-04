"""End-to-end API tests for Watch Once.

usage: python api_test.py BASE_URL MEDIA_DIR [--limits]
  default run: every endpoint, media, uploads (mp4, HEVC .mov, rotated), error paths
  --limits:    run against a server started with MAX_UPLOAD_MB=1 MAX_VIDEO_S=6
               JOBS_PER_HOUR_PER_IP=3 to check the abuse limits
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

BASE, MEDIA = sys.argv[1].rstrip("/"), Path(sys.argv[2])
LIMITS = "--limits" in sys.argv
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def get(path, **kw):
    return requests.get(BASE + path, timeout=60, **kw)


def upload(path, file):
    with open(MEDIA / file, "rb") as f:
        return requests.post(BASE + path, files={"file": (file, f, "video/mp4")}, timeout=300)


def wait(job, limit=600):
    t = time.time()
    while time.time() - t < limit:
        j = get(f"/api/jobs/{job}").json()
        if j["state"] != "running":
            return j
        time.sleep(3)
    return {"state": "timeout"}


def media_ok(url, ctype):
    r = get(url)
    return r.status_code == 200 and r.headers.get("content-type", "").startswith(ctype) and len(r.content) > 0


def probe(url):
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(get(url).content)
        f.flush()
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name,width,height", "-of", "json", f.name], capture_output=True, text=True)
        return json.loads(out.stdout)["streams"][0]


def check_procedure(pid, label):
    p = get(f"/api/procedures/{pid}")
    if not check(f"{label}: procedure JSON", p.status_code == 200, p.status_code):
        return None
    p = p.json()
    check(f"{label}: has steps", len(p["steps"]) >= 1, len(p["steps"]))
    check(f"{label}: expert video served", media_ok(f"{p['media']}/expert.mp4", "video/mp4"))
    check(f"{label}: every step frame served", all(media_ok(f"{p['media']}/{s['frame']}", "image/jpeg") for s in p["steps"]))
    return p


def check_report(pid, aid, label):
    r = get(f"/api/procedures/{pid}/attempts/{aid}")
    if not check(f"{label}: report JSON", r.status_code == 200, r.status_code):
        return None
    r = r.json()
    check(f"{label}: raw runs not exposed", "runs" not in r)
    check(f"{label}: verdicts valid", all(s["status"] in {"done", "skipped", "out_of_order", "incorrect", "uncertain"} for s in r["steps"]))
    check(f"{label}: attempt video + poster served",
          media_ok(f"{r['media']}/attempt.mp4", "video/mp4") and media_ok(f"{r['media']}/poster.jpg", "image/jpeg"))
    check(f"{label}: evidence frames served", all(media_ok(f"{r['media']}/{s['frame']}", "image/jpeg") for s in r["steps"] if s.get("frame")))
    return r


if LIMITS:
    r = upload("/api/procedures", "big.mp4")  # 15 MB: rejected while uploading
    check("upload over size limit is refused with 413", r.status_code == 413, r.status_code)
    r = upload("/api/procedures", "eightsec.mp4")  # small file but 8 s > 6 s limit
    j = wait(r.json()["job"]) if r.ok else {}
    check("video over length limit fails with a clear message", j.get("state") == "error" and "longer than" in j.get("error", ""), j)
    codes = [upload("/api/procedures", "notvideo.mp4").status_code for _ in range(3)]
    check("per-IP hourly limit returns 429", 429 in codes, codes)
    sys.exit(0 if all(results) else 1)

# Page and static assets
r = get("/")
check("index page", r.status_code == 200 and "Watch" in r.text and "/static/app.js" in r.text, r.status_code)
check("app.js served", media_ok("/static/app.js", "text/javascript") or media_ok("/static/app.js", "application/javascript"))
check("style.css served", media_ok("/static/style.css", "text/css"))

# Bundled sample
samples = get("/api/samples").json()
check("samples list the bundled procedure", any(s["id"] == "pack-the-tray" and "skipped-blue" in s["attempts"] for s in samples), samples)
p = check_procedure("pack-the-tray", "sample")
check_report("pack-the-tray", "skipped-blue", "sample attempt")
rng = get("/media/d/pack-the-tray/expert.mp4", headers={"Range": "bytes=0-1023"})
check("video supports range requests (needed for seeking)", rng.status_code == 206 and len(rng.content) == 1024, rng.status_code)

# Not-found and traversal attempts
for path in ["/api/procedures/nope", "/api/procedures/..%2F..%2Fetc", "/api/procedures/pack-the-tray/attempts/..%2F..",
             "/api/jobs/nope", "/api/jobs/..%2Fx", "/media/u/../../etc/passwd", "/media/d/..%2F..%2Fwatchonce%2Fapp.py"]:
    code = get(path).status_code
    check(f"404 for {path}", code == 404, code)

# Security headers
h = get("/").headers
check("security headers set (CSP, nosniff, frame-ancestors)",
      "default-src 'self'" in h.get("content-security-policy", "") and h.get("x-content-type-options") == "nosniff"
      and "frame-ancestors 'none'" in h.get("content-security-policy", ""), dict(h))

# Bad uploads
r = upload("/api/procedures", "notvideo.mp4")
j = wait(r.json()["job"]) if r.ok else {}
check("non-video upload fails with a readable error", j.get("state") == "error" and "couldn't be read" in j.get("error", ""), j)
check("error does not leak server paths", "/" not in j.get("error", "x/"), j)
r = upload("/api/procedures", "playlist.bin")
j = wait(r.json()["job"]) if r.ok else {}
check("HLS playlist pointing at /etc/passwd is rejected", j.get("state") == "error", j)

# Teach a new procedure and check attempts in phone formats
r = upload("/api/procedures", "expert.mp4")
j = wait(r.json()["job"])
if check("expert upload completes", j["state"] == "done", j):
    pid = j["procedure_id"]
    check_procedure(pid, "uploaded procedure")
    for file, label in [("trainee.mp4", "mp4 attempt"), ("phone_hevc.mov", "HEVC .mov attempt"), ("rotated.mp4", "rotated phone attempt")]:
        r = upload(f"/api/procedures/{pid}/attempts", file)
        j = wait(r.json()["job"])
        if check(f"{label} completes", j["state"] == "done", j):
            rep = check_report(pid, j["attempt_id"], label)
            if label.startswith("rotated") and rep:
                s = probe(f"{rep['media']}/attempt.mp4")
                check("rotated video comes out portrait", s["height"] > s["width"], s)
            if label == "mp4 attempt" and rep:
                bad = {x["id"] for x in rep["steps"] if x["status"] in ("skipped", "out_of_order")}
                check("staged mistakes caught (blue skipped)", 2 in bad, [(x["id"], x["status"]) for x in rep["steps"]])

# Checking an attempt against the bundled sample copies it into user storage
r = upload("/api/procedures/pack-the-tray/attempts", "trainee.mp4")
j = wait(r.json()["job"])
if check("attempt on the bundled sample completes", j["state"] == "done", j):
    check_report("pack-the-tray", j["attempt_id"], "attempt on sample")
    check_procedure("pack-the-tray", "sample after copy")
    check("bundled sample attempt still listed", "skipped-blue" in next(s for s in get("/api/samples").json() if s["id"] == "pack-the-tray")["attempts"])
    check_report("pack-the-tray", "skipped-blue", "bundled attempt after copy")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
