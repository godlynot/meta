"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Timecode as mm:ss.s, the way an editor reads a clip.
const tc = (s) => s == null ? "--:--.-"
  : `${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const two = (n) => String(n).padStart(2, "0");

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
        <span class="label">STEP ${two(s.id)}${s.critical ? ' <span class="critical">Critical</span>' : ""}</span>
        <span class="title">${esc(s.title)}</span>
      </span>
    </button></li>`).join("");
  for (const card of $("steps").querySelectorAll(".step-card")) {
    card.onclick = () => playRange(video, +card.dataset.start, +card.dataset.end);
  }
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
    sampleButton(`Sample attempt: ${aid}`, () =>
      loadReport(aid).catch((e) => showProgress($("check-progress"), e.message, true)))));
}

function issueCard(s) {
  const ref = procedure.steps.find((x) => x.id === s.id);
  const when = s.status === "skipped"
    ? `Never happened · noticed at ${tc(s.alert_s)}`
    : `At ${tc(s.start_s)} in the attempt`;
  const yours = s.frame
    ? `<figure><img src="${report.media}/${esc(s.frame)}" alt="Frame from the attempt">
         <figcaption class="label">The attempt${s.outlined ? " · outlined by SAM 3.1" : ""}</figcaption></figure>`
    : "";
  const expert = ref
    ? `<figure><video src="${procedure.media}/expert.mp4#t=${ref.start_s},${ref.end_s}" poster="${procedure.media}/${esc(ref.frame)}"
         controls playsinline preload="none"></video>
         <figcaption class="label">How the expert does it · ${tc(ref.start_s)}</figcaption></figure>`
    : "";
  return `<article class="issue">
    <div class="issue-head">
      <span class="stamp ${s.status}">${LABEL[s.status]}</span>
      ${s.critical ? '<span class="critical">Critical</span>' : ""}
      ${agreement(s.agreement)}
      <h4>Step ${two(s.id)} · ${esc(s.title)}</h4>
    </div>
    <div class="meta">${when}</div>
    <p>${esc(s.evidence)}</p>
    ${s.fix ? `<p class="fix"><strong>Fix:</strong> ${esc(s.fix)}</p>` : ""}
    <div class="pair">${yours}${expert}</div>
  </article>`;
}

async function loadReport(aid, scroll = true) {
  stopReplay();
  report = await api(`/api/procedures/${procedure.id}/attempts/${aid}`);
  const steps = report.steps;
  const ok = steps.filter((s) => s.status === "done").length;
  const issues = steps.filter((s) => !["done", "uncertain"].includes(s.status));
  const unclear = steps.length - ok - issues.length;
  $("score").innerHTML = `${ok}<small>/${steps.length}</small>`;
  $("score-main").textContent = "steps done right";
  $("score-sub").textContent = [`${issues.length} to fix`, unclear ? `${unclear} unclear` : ""].filter(Boolean).join(" · ");
  const video = $("attempt-video");
  video.src = `${report.media}/attempt.mp4`;
  video.poster = `${report.media}/poster.jpg`;
  video.controls = true;
  $("issues").innerHTML = issues.length ? issues.map(issueCard).join("")
    : "<p>Nothing to fix. Every step was done correctly and in order.</p>";
  $("checklist").innerHTML = steps.map((s) => `
    <li>
      <span class="n">${two(s.id)}</span>
      <span class="what">${esc(s.title)} <span class="stamp ${s.status}">${LABEL[s.status]}</span></span>
      ${agreement(s.agreement)}
    </li>`).join("");
  $("extras").innerHTML = report.extra_actions.length
    ? `<h3>Not in the procedure</h3><ul>${report.extra_actions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  $("cost").textContent = `${report.votes} independent checks by Muse Spark · analysis cost about $${report.cost_usd.toFixed(2)}`;
  $("report").hidden = false;
  setUrl();
  if (scroll) $("report").scrollIntoView({ behavior: "smooth", block: "start" });
}

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
    await say(`${headline} ${detail}`);
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

(async () => {
  const samples = await api("/api/samples").catch(() => []);
  $("sample-procedures").replaceChildren(...samples.map((s) => sampleButton(`Try the sample: ${s.task}`, () =>
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
