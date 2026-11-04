"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Timecode as mm:ss.s, the way an editor reads a clip.
const tc = (s) => s == null ? "--:--.-"
  : `${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const two = (n) => String(n).padStart(2, "0");
// Bundled samples are named like "skipped-jelly"; show that as "Skipped jelly".
const pretty = (slug) => slug.charAt(0).toUpperCase() + slug.slice(1).replace(/[-_]+/g, " ");

// Only ever put known-safe IDs into request paths.
const safeId = (v) => (/^[A-Za-z0-9_-]{1,64}$/.test(v ?? "") ? v : null);
const LABEL = { done: "Correct", skipped: "Skipped", out_of_order: "Wrong order", incorrect: "Done wrong", uncertain: "Unclear" };
let procedure = null;
let report = null;
let stopReplay = () => {};

async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body;
}

function showProgress(el, text, error = false) {
  el.hidden = false;
  el.className = error ? "progress error" : "progress";
  el.innerHTML = error ? esc(text) : `<span class="spin"></span>${esc(text)}`;
}

async function waitFor(jobId, el) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.state === "done") { el.hidden = true; return job; }
    if (job.state === "error") throw new Error(job.error);
    showProgress(el, `${job.stage}…`);
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function upload(url, file, el) {
  showProgress(el, `Uploading ${file.name}…`);
  const form = new FormData();
  form.append("file", file);
  const { job } = await api(url, { method: "POST", body: form });
  return waitFor(job, el);
}

function setUrl() {
  const q = new URLSearchParams();
  if (procedure) q.set("p", procedure.id);
  if (report) q.set("a", report.id);
  history.replaceState(null, "", q.toString() ? `?${q}` : location.pathname);
}

// Play [start, end] of a video element, then pause.
function playRange(video, start, end) {
  video.currentTime = start;
  video.play();
  const stop = () => {
    if (video.currentTime >= end) { video.pause(); video.removeEventListener("timeupdate", stop); }
  };
  video.addEventListener("timeupdate", stop);
}

function agreement(text) {
  const [n, of] = String(text).split("/").map(Number);
  const dots = Array.from({ length: of || 0 }, (_, i) => `<i class="${i < n ? "on" : ""}"></i>`).join("");
  return `<span class="agree meta"><span class="dots" aria-hidden="true">${dots}</span>${n}/${of} checks agree</span>`;
}

function sampleButton(text, onclick) {
  const b = document.createElement("button");
  b.className = "button";
  b.type = "button";
  b.textContent = text;
  b.onclick = onclick;
  return b;
}

async function loadProcedure(pid) {
  stopReplay();
  if (!safeId(pid)) throw new Error("That link doesn't point to a procedure.");
  procedure = await api(`/api/procedures/${pid}`);
  report = null;
  $("task-title").textContent = procedure.task;
  const video = $("expert-video");
  video.src = `${procedure.media}/expert.mp4`;
  video.poster = procedure.steps.length ? `${procedure.media}/${procedure.steps[0].frame}` : "";
  $("steps").innerHTML = procedure.steps.map((s) => `
    <li><button class="step-card" type="button" data-start="${s.start_s}" data-end="${s.end_s}"
        aria-label="Play step ${s.id}: ${esc(s.title)}">
      <span class="shot">
        <img src="${procedure.media}/${esc(s.frame)}" alt="" loading="lazy">
        <span class="timecode">${tc(s.start_s)}</span>
      </span>
      <span class="body">
        <span class="label"><span class="step-no">STEP ${two(s.id)}</span>${s.critical ? ' <span class="critical">Critical</span>' : ""}</span>
        <span class="title">${esc(s.title)}</span>
      </span>
    </button></li>`).join("");
  for (const card of $("steps").querySelectorAll(".step-card")) {
    card.onclick = () => playRange(video, +card.dataset.start, +card.dataset.end);
  }
  if (live.running) await stopLive();
  $("live").hidden = true;
  $("procedure").hidden = false;
  $("check").hidden = false;
  $("report").hidden = true;
  await loadSampleAttempts();
  setUrl();
}

async function loadSampleAttempts() {
  const samples = await api("/api/samples");
  const mine = samples.find((s) => s.id === procedure.id);
  $("sample-attempts").replaceChildren(...(mine ? mine.attempts : []).map((aid) =>
    sampleButton(`Sample: ${pretty(aid)}`, () =>
      loadReport(aid).catch((e) => showProgress($("check-progress"), e.message, true)))));
}

function issueCard(s) {
  const ref = procedure.steps.find((x) => x.id === s.id);
  const when = s.status === "skipped"
    ? `Never done. A coach would notice at ${tc(s.alert_s)}.`
    : `Happens at ${tc(s.start_s)} in the attempt.`;
  const yours = s.frame
    ? `<figure><img src="${report.media}/${esc(s.frame)}" alt="Frame from the attempt">
         <figcaption class="label">${s.outlined ? "The attempt, with SAM 3.1's outline" : "The attempt"}</figcaption></figure>`
    : "";
  const expert = ref
    ? `<figure><video src="${procedure.media}/expert.mp4#t=${ref.start_s},${ref.end_s}" poster="${procedure.media}/${esc(ref.frame)}"
         controls playsinline preload="none"></video>
         <figcaption class="label">The expert doing it right</figcaption></figure>`
    : "";
  return `<article class="issue">
    <div class="issue-head">
      <span class="stamp ${s.status}">${LABEL[s.status]}</span>
      ${s.critical ? '<span class="critical">Critical</span>' : ""}
      ${agreement(s.agreement)}
      <h4>Step ${s.id}: ${esc(s.title)}</h4>
    </div>
    <div class="meta">${when}</div>
    <p>${esc(s.evidence)}</p>
    ${s.fix ? `<p class="fix"><strong>Fix:</strong> ${esc(s.fix)}</p>` : ""}
    <div class="pair">${yours}${expert}</div>
  </article>`;
}

async function loadReport(aid, scroll = true) {
  stopReplay();
  if (!safeId(aid)) throw new Error("That link doesn't point to a checked attempt.");
  report = await api(`/api/procedures/${procedure.id}/attempts/${aid}`);
  const steps = report.steps;
  const ok = steps.filter((s) => s.status === "done").length;
  const issues = steps.filter((s) => !["done", "uncertain"].includes(s.status));
  const unclear = steps.length - ok - issues.length;
  $("score").innerHTML = `${ok}<small>/${steps.length}</small>`;
  $("score-main").textContent = "steps done right";
  $("score-sub").textContent = `${issues.length} to fix` + (unclear ? `, ${unclear} unclear` : "");
  const video = $("attempt-video");
  video.src = `${report.media}/attempt.mp4`;
  video.poster = `${report.media}/poster.jpg`;
  video.controls = true;
  $("issues").innerHTML = issues.length ? issues.map(issueCard).join("")
    : "<p>Nothing to fix. Every step was done correctly and in order.</p>";
  $("checklist").innerHTML = steps.map((s) => `
    <li>
      <span class="n step-no">${two(s.id)}</span>
      <span class="what">${esc(s.title)} <span class="stamp ${s.status}">${LABEL[s.status]}</span></span>
      ${agreement(s.agreement)}
    </li>`).join("");
  $("extras").innerHTML = report.extra_actions.length
    ? `<h3>Not in the procedure</h3><ul>${report.extra_actions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  $("cost").textContent = `Checked ${report.votes} times by Muse Spark. This analysis cost about $${report.cost_usd.toFixed(2)}.`;
  if (!live.running) $("live").hidden = true;
  $("report").hidden = false;
  setUrl();
  if (scroll) $("report").scrollIntoView({ behavior: "smooth", block: "start" });
}

// Alerts stay on screen long enough to read, even when there is no voice to wait for.
const MIN_ALERT_MS = 2500;
const linger = (ms) => new Promise((r) => setTimeout(r, ms));

function say(text) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return setTimeout(resolve, 2500);
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    u.onend = resolve;
    u.onerror = resolve;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
    setTimeout(resolve, 9000); // never hang if the voice never finishes
  });
}

function alertText(s) {
  if (s.status === "skipped") return ["Skipped", `Hold on. You skipped step ${s.id}.`, s.title];
  if (s.status === "out_of_order") return ["Order", `Wait. Step ${s.id} is out of order.`, s.title];
  return ["Stop", `Stop. Step ${s.id} isn't right.`, s.fix || s.title];
}

function replay() {
  stopReplay();
  const video = $("attempt-video");
  const coach = $("coach");
  const alerts = report.steps.filter((s) => s.alert_s != null).sort((a, b) => a.alert_s - b.alert_s);
  let next = 0;
  let busy = false;
  coach.hidden = true;
  video.controls = false;
  video.currentTime = 0;
  window.speechSynthesis?.cancel();

  const fire = async (force) => {
    if (busy || next >= alerts.length) return;
    if (!force && video.currentTime < alerts[next].alert_s) return;
    busy = true;
    const s = alerts[next++];
    const [tag, headline, detail] = alertText(s);
    video.pause();
    coach.innerHTML = `<span class="tag">${esc(tag)}</span><strong>${esc(headline)}</strong><span>${esc(detail)}</span>`;
    coach.hidden = false;
    await Promise.all([say(`${headline} ${detail}`), linger(MIN_ALERT_MS)]);
    coach.hidden = true;
    busy = false;
    if (!force && !video.ended) video.play();
  };
  const onTime = () => fire(false);
  const onEnded = async () => {
    // Alerts at the very end (e.g. a jar left open) fire after playback stops.
    while (next < alerts.length) {
      while (busy) await new Promise((r) => setTimeout(r, 100));
      await fire(true);
    }
    stopReplay();
  };
  stopReplay = () => {
    video.removeEventListener("timeupdate", onTime);
    video.removeEventListener("ended", onEnded);
    video.controls = true;
    stopReplay = () => {};
  };
  video.addEventListener("timeupdate", onTime);
  video.addEventListener("ended", onEnded);
  video.play();
}

async function pickFile(input, url, progress, onDone) {
  const file = input.files[0];
  if (!file) return;
  try {
    await onDone(await upload(url, file, progress));
  } catch (err) {
    showProgress(progress, err.message, true);
  }
  input.value = "";
}

$("expert-btn").onclick = () => $("expert-file").click();
$("attempt-btn").onclick = () => $("attempt-file").click();
$("expert-file").onchange = (e) => pickFile(e.target, "/api/procedures", $("teach-progress"),
  (job) => loadProcedure(job.procedure_id));
$("attempt-file").onchange = (e) => procedure && pickFile(e.target, `/api/procedures/${procedure.id}/attempts`,
  $("check-progress"), (job) => loadReport(job.attempt_id));
$("replay").onclick = replay;

// Live coaching: record the camera in short clips, send each one, speak the alerts.
// Two recorders run 4 s apart, so a new 8-second clip goes out every 4 seconds.
const CLIP_MS = 8000;
const live = { running: false, session: null, stream: null, recorders: [], pending: 0, queue: [], speaking: false };
const LIVE_LABEL = { ...LABEL, pending: "Not yet" };

function pickMime() {
  const options = ["video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  return options.find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
}

function renderLive(state) {
  $("live-steps").innerHTML = procedure.steps.map((s) => {
    const st = state.status[s.id] || "pending";
    return `<li class="${state.working_on === s.id ? "now" : ""}">
      <span class="n step-no">${two(s.id)}</span>
      <span class="what">${esc(s.title)} <span class="stamp ${st}">${LIVE_LABEL[st]}</span></span>
    </li>`;
  }).join("");
}

async function speakQueue() {
  if (live.speaking) return;
  live.speaking = true;
  const coach = $("live-coach");
  while (live.queue.length) {
    const a = live.queue.shift();
    const tag = a.kind === "incorrect" ? "Stop" : "Hold on";
    coach.innerHTML = `<span class="tag">${esc(tag)}</span><strong>${esc(a.message)}</strong><span>${esc(a.title)}</span>`;
    coach.hidden = false;
    await Promise.all([say(`${tag}. ${a.message}`), linger(MIN_ALERT_MS)]);
    await linger(600);
  }
  coach.hidden = true;
  live.speaking = false;
}

function pushAlerts(alerts) {
  if (!alerts?.length) return;
  live.queue.push(...alerts);
  speakQueue();
}

async function sendClip(blob, type) {
  live.pending++;
  const form = new FormData();
  form.append("file", blob, type.startsWith("video/mp4") ? "clip.mp4" : "clip.webm");
  try {
    const r = await api(`/api/procedures/${procedure.id}/live/${live.session}/clips`, { method: "POST", body: form });
    renderLive(r.state);
    pushAlerts(r.alerts);
    $("live-status").textContent = r.error
      || `Watching. ${r.state.done} of ${r.state.total} steps seen so far.`;
  } catch (err) {
    $("live-status").textContent = err.message;
    if (/ended|15 minutes/.test(err.message)) stopLive();
  } finally {
    live.pending--;
  }
}

function recordClip(type, lane) {
  if (!live.running) return;
  const parts = [];
  const rec = new MediaRecorder(live.stream, type ? { mimeType: type, videoBitsPerSecond: 1500000 } : undefined);
  live.recorders[lane] = rec;
  rec.ondataavailable = (e) => e.data.size && parts.push(e.data);
  rec.onstop = () => {
    if (parts.length) sendClip(new Blob(parts, { type: rec.mimeType || type }), rec.mimeType || type);
    recordClip(type, lane);  // start the next clip straight away so nothing is missed
  };
  rec.start();
  setTimeout(() => rec.state === "recording" && rec.stop(), CLIP_MS);
}

async function startLive() {
  if (!procedure) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showProgress($("check-progress"), "This browser can't record from the camera. Try Chrome or Safari, or upload a video instead.", true);
    return;
  }
  stopReplay();
  try {
    live.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false,
    });
  } catch (err) {
    showProgress($("check-progress"), "Camera access was blocked. Allow the camera for this site and try again.", true);
    return;
  }
  let started;
  try {
    started = await api(`/api/procedures/${procedure.id}/live`, { method: "POST" });
  } catch (err) {
    live.stream.getTracks().forEach((t) => t.stop());
    showProgress($("check-progress"), err.message, true);
    return;
  }
  window.speechSynthesis?.speak(new SpeechSynthesisUtterance(""));  // unlock speech while we still have the click
  live.session = started.session;
  live.running = true;
  live.queue = [];
  $("check-progress").hidden = true;
  $("report").hidden = true;
  $("live").hidden = false;
  $("live-badge").classList.remove("off");
  $("live-badge").textContent = "Live";
  $("live-video").srcObject = live.stream;
  $("live-stop").disabled = false;
  $("live-btn").disabled = true;
  $("live-status").textContent = "Watching. Start the task whenever you're ready.";
  renderLive(started.state);
  $("live").scrollIntoView({ behavior: "smooth", block: "start" });
  const type = pickMime();
  recordClip(type, 0);
  setTimeout(() => recordClip(type, 1), CLIP_MS / 2);
  // Also keep the whole session, so Stop can run the full three-check review on it.
  const full = new MediaRecorder(live.stream, type ? { mimeType: type, videoBitsPerSecond: 1000000 } : undefined);
  live.full = { rec: full, parts: [], type, done: new Promise((r) => { full.onstop = r; }) };
  full.ondataavailable = (e) => e.data.size && live.full.parts.push(e.data);
  full.start(1000);
}

async function stopLive() {
  if (!live.running) return;
  live.running = false;
  $("live-stop").disabled = true;
  $("live-status").textContent = "Checking the last few seconds…";
  for (const rec of live.recorders) if (rec?.state === "recording") rec.stop();
  live.recorders = [];
  if (live.full?.rec.state === "recording") live.full.rec.stop();
  await live.full?.done;
  await new Promise((r) => setTimeout(r, 300));
  while (live.pending) await new Promise((r) => setTimeout(r, 200));
  live.stream?.getTracks().forEach((t) => t.stop());
  $("live-badge").classList.add("off");
  $("live-badge").textContent = "Stopped";
  try {
    const r = await api(`/api/procedures/${procedure.id}/live/${live.session}/end`, { method: "POST" });
    renderLive(r.state);
    pushAlerts(r.alerts);
    const ok = Object.values(r.state.status).filter((s) => s === "done").length;
    $("live-status").textContent = `Session over: ${ok} of ${r.state.total} steps done right. `
      + `${r.clips} moments checked for about $${r.cost_usd.toFixed(2)}.`;
  } catch (err) {
    $("live-status").textContent = err.message;
  }
  // Live alerts are quick; the full review of the whole session is the thorough one.
  const parts = live.full?.parts || [];
  if (parts.length) {
    const type = live.full.rec.mimeType || live.full.type || "video/webm";
    const file = new File(parts, type.includes("mp4") ? "live-session.mp4" : "live-session.webm", { type });
    try {
      const job = await upload(`/api/procedures/${procedure.id}/attempts`, file, $("check-progress"));
      await loadReport(job.attempt_id);
    } catch (err) {
      showProgress($("check-progress"), `The full review didn't run: ${err.message}`, true);
    }
  }
  live.full = null;
  $("live-btn").disabled = false;
}

$("live-btn").onclick = startLive;
$("live-stop").onclick = stopLive;

(async () => {
  const samples = await api("/api/samples").catch(() => []);
  $("sample-procedures").replaceChildren(...samples.map((s) => sampleButton(`Load sample: ${pretty(s.id)}`, () =>
    loadProcedure(s.id).catch((e) => showProgress($("teach-progress"), e.message, true)))));
  const q = new URLSearchParams(location.search);
  try {
    if (q.get("p")) {
      await loadProcedure(q.get("p"));
      if (q.get("a")) await loadReport(q.get("a"), false);
    } else if (samples.length) {
      // Open in a working state: the first sample and its first checked attempt.
      await loadProcedure(samples[0].id);
      if (samples[0].attempts.length) await loadReport(samples[0].attempts[0], false);
    }
  } catch (e) {
    showProgress($("teach-progress"), e.message, true);
  }
})();
