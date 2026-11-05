// What a judge might do, and what must (not) happen. Live-coaching scenarios with
// a fake webcam feed, plus edge-case uploads.
// usage: node tests/robustness.js BASE_URL FEEDS_DIR [scenario ...]
//   FEEDS_DIR holds nutella_correct.y4m, tray_correct.y4m, idle.y4m, blank.mp4,
//   audio_only.m4a and clip.webm (see tests/README.md)
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [BASE, FEEDS, ...only] = process.argv.slice(2);
const results = [];
const check = (name, ok, detail = "") => {
  results.push(!!ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? "  (" + JSON.stringify(detail).slice(0, 500) + ")" : ""}`);
};

async function liveSession(feed, pid, seconds) {
  const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    `--use-file-for-fake-video-capture=${path.join(FEEDS, feed)}`, "--autoplay-policy=no-user-gesture-required"] });
  const page = await (await browser.newContext({ permissions: ["camera"] })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${BASE}/?p=${pid}`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    window.__spoken = [];
    window.__t0 = performance.now();
    window.speechSynthesis.speak = (u) => { if (u.text) window.__spoken.push([Math.round((performance.now() - window.__t0) / 1000), u.text]);
      setTimeout(() => u.onend && u.onend(), 200); };
  });
  await page.click("#live-btn");
  await page.waitForTimeout(seconds * 1000);
  const duringCount = await page.evaluate(() => window.__spoken.length);
  await page.click("#live-stop");
  await page.waitForFunction(() => /Session over|didn't see/.test(document.getElementById("live-status").textContent), null, { timeout: 120000 }).catch(() => {});
  const reviewed = await page.waitForSelector("#report:not([hidden])", { timeout: 240000 }).then(() => true).catch(() => false);
  const out = await page.evaluate(() => ({
    status: document.getElementById("live-status").textContent,
    spoken: window.__spoken,
    score: document.getElementById("report").hidden ? null : document.getElementById("score").textContent,
    issues: [...document.querySelectorAll(".issue h4")].map((e) => e.textContent),
  }));
  await browser.close();
  return { ...out, duringCount, reviewed, errors };
}

async function upload(endpoint, file) {
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(path.join(FEEDS, file))]), file);
  const r = await fetch(BASE + endpoint, { method: "POST", body: form });
  const body = await r.json();
  if (!r.ok) return { state: "rejected", status: r.status, error: body.detail };
  for (;;) {
    const j = await (await fetch(`${BASE}/api/jobs/${body.job}`)).json();
    if (j.state !== "running") return j;
    await new Promise((res) => setTimeout(res, 3000));
  }
}

const scenarios = {
  // 1. Doing the task right must not trigger a single alert.
  async nutella_correct() {
    const r = await liveSession("nutella_correct.y4m", "nutella-sandwich", 112);
    console.log("  spoken:", JSON.stringify(r.spoken), "| review:", r.score, r.issues);
    check("correct Nutella run: no alerts while working", r.spoken.filter(([t]) => t <= 112).length === 0, r.spoken);
    check("correct Nutella run: review finds at most one issue", r.reviewed && r.issues.length <= 1, r.issues);
    check("correct Nutella run: no page errors", r.errors.length === 0, r.errors);
  },
  async tray_correct() {
    const r = await liveSession("tray_correct.y4m", "pack-the-tray", 18);
    console.log("  spoken:", JSON.stringify(r.spoken), "| review:", r.score, r.issues);
    check("correct tray run: no alerts", r.spoken.length === 0, r.spoken);
    check("correct tray run: review finds no issues", r.reviewed && r.issues.length === 0, r.issues);
  },
  // 2. Pointing the camera at a scene where nothing happens.
  async idle() {
    const r = await liveSession("idle.y4m", "nutella-sandwich", 30);
    console.log("  spoken:", JSON.stringify(r.spoken), "| status:", r.status);
    check("idle camera: no alerts while nothing happens", r.duringCount === 0, r.spoken);
    check("idle camera: calm 'didn't see any steps' message, not a list of misses", /didn't see/.test(r.status) && r.spoken.length === 0, r);
    check("idle camera: no full review of nothing", !r.reviewed);
  },
  // 3. Pressing Coach me live and stopping straight away.
  async quick_stop() {
    const r = await liveSession("idle.y4m", "ram-install", 3);
    console.log("  spoken:", JSON.stringify(r.spoken), "| status:", r.status);
    check("quick stop: no spoken list of steps", r.spoken.length === 0, r.spoken);
    check("quick stop: calm message", /didn't see/.test(r.status), r.status);
    check("quick stop: no review", !r.reviewed);
  },
  // 4. Uploads that aren't a procedure.
  async bad_uploads() {
    const blank = await upload("/api/procedures", "blank.mp4");
    check("video with no task in it: clear message", blank.state === "error" && /couldn't find a hands-on task/.test(blank.error), blank);
    const audio = await upload("/api/procedures", "audio_only.m4a");
    check("audio-only file: rejected straight away", audio.state === "error" && /no picture/.test(audio.error), audio);
  },
  // 5. Firefox records WebM: the live endpoint must still work.
  async webm_clip() {
    const s = await (await fetch(`${BASE}/api/procedures/pack-the-tray/live`, { method: "POST" })).json();
    const form = new FormData();
    form.append("file", new Blob([fs.readFileSync(path.join(FEEDS, "clip.webm"))]), "clip.webm");
    const r = await (await fetch(`${BASE}/api/procedures/pack-the-tray/live/${s.session}/clips`, { method: "POST", body: form })).json();
    check("WebM live clip (Firefox) is understood", r.error === null && r.state, r);
    await fetch(`${BASE}/api/procedures/pack-the-tray/live/${s.session}/end`, { method: "POST" });
  },
};

(async () => {
  for (const [name, run] of Object.entries(scenarios)) {
    if (only.length && !only.includes(name)) continue;
    console.log(`--- ${name}`);
    try { await run(); } catch (e) { check(`${name} ran without crashing`, false, e.message); }
  }
  console.log(`\n${results.filter(Boolean).length}/${results.length} passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})();
