"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clock = (s) => s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const LABEL = { done: "Correct", skipped: "Skipped", out_of_order: "Wrong order", incorrect: "Done wrong", uncertain: "Unclear" };
let procedure = null;
let report = null;

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

async function loadProcedure(pid) {
  procedure = await api(`/api/procedures/${pid}`);
  report = null;
  $("task-title").textContent = procedure.task;
  const video = $("expert-video");
  video.src = `${procedure.media}/expert.mp4`;
  video.poster = procedure.steps.length ? `${procedure.media}/${procedure.steps[0].frame}` : "";
  $("steps").innerHTML = procedure.steps.map((s) => `
    <li data-start="${s.start_s}" data-end="${s.end_s}">
      <img src="${procedure.media}/${esc(s.frame)}" alt="" loading="lazy">
      <div class="body">
        <div class="meta">Step ${s.id} · ${clock(s.start_s)}${s.critical ? ' · <span class="critical">critical</span>' : ""}</div>
        <div>${esc(s.title)}</div>
      </div>
    </li>`).join("");
  for (const li of $("steps").children) {
    li.onclick = () => playRange(video, +li.dataset.start, +li.dataset.end);
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
  $("sample-attempts").innerHTML = "";
  for (const aid of mine ? mine.attempts : []) {
    const b = document.createElement("button");
    b.className = "button";
    b.textContent = `Sample attempt: ${aid}`;
    b.onclick = () => loadReport(aid).catch((e) => showProgress($("check-progress"), e.message, true));
    $("sample-attempts").append(b);
  }
}

function issueCard(s) {
  const ref = procedure.steps.find((x) => x.id === s.id);
  const when = s.status === "skipped"
    ? `Never happened · noticed at ${clock(s.alert_s)}`
    : `At ${clock(s.start_s)} in the attempt`;
  const yours = s.frame
    ? `<div><div class="label">The attempt${s.outlined ? " · outlined by SAM 3.1" : ""}</div>
         <img src="${report.media}/${esc(s.frame)}" alt=""></div>`
    : "<div></div>";
  const expert = ref
    ? `<div><div class="label">How the expert does it (${clock(ref.start_s)})</div>
         <video src="${procedure.media}/expert.mp4#t=${ref.start_s},${ref.end_s}" poster="${procedure.media}/${esc(ref.frame)}" controls playsinline preload="metadata"></video></div>`
    : "";
  return `<div class="issue ${s.status}">
    <span class="chip ${s.status}">${LABEL[s.status]}</span>
    ${s.critical ? '<span class="critical">critical step</span>' : ""}
    <h4>Step ${s.id}: ${esc(s.title)}</h4>
    <div class="meta">${when} · ${s.agreement} checks agree</div>
    <p>${esc(s.evidence)}</p>
    ${s.fix ? `<p><strong>Fix:</strong> ${esc(s.fix)}</p>` : ""}
    <div class="pair">${yours}${expert}</div>
  </div>`;
}

async function loadReport(aid) {
  stopReplay();
  report = await api(`/api/procedures/${procedure.id}/attempts/${aid}`);
  const steps = report.steps;
  const ok = steps.filter((s) => s.status === "done").length;
  const issues = steps.filter((s) => !["done", "uncertain"].includes(s.status));
  const unclear = steps.length - ok - issues.length;
  $("summary").textContent = `${ok} of ${steps.length} steps correct · ${issues.length} to fix`
    + (unclear ? ` · ${unclear} unclear` : "");
  $("attempt-video").src = `${report.media}/attempt.mp4`;
  $("attempt-video").controls = true;
  $("issues").innerHTML = issues.length ? issues.map(issueCard).join("") : "<p>Nothing to fix. Every step was done correctly and in order.</p>";
  $("checklist").innerHTML = steps.map((s) => {
    const cls = s.status === "done" ? "done" : s.status === "uncertain" ? "uncertain" : "bad";
    const mark = { done: "✓", uncertain: "?" }[s.status] || "✗";
    return `<li class="${cls}"><span class="mark">${mark}</span>
      <span>${s.id}. ${esc(s.title)} <span class="chip ${s.status}">${LABEL[s.status]}</span></span>
      <span class="agree">${s.agreement} agree</span></li>`;
  }).join("");
  $("extras").innerHTML = report.extra_actions.length
    ? `<h3>Not in the procedure</h3><ul>${report.extra_actions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  $("cost").textContent = `${report.votes} independent checks by Muse Spark · analysis cost about $${report.cost_usd.toFixed(2)}`;
  $("report").hidden = false;
  setUrl();
  $("report").scrollIntoView({ behavior: "smooth", block: "start" });
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
  if (s.status === "skipped") return [`Hold on. You skipped step ${s.id}.`, s.title];
  if (s.status === "out_of_order") return [`Wait. Step ${s.id} is out of order.`, s.title];
  return [`Stop. Step ${s.id} isn't right.`, s.fix || s.title];
}

let stopReplay = () => {};

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
    const [headline, detail] = alertText(s);
    video.pause();
    coach.innerHTML = `${esc(headline)}<small>${esc(detail)}</small>`;
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

async function loadSamples() {
  const samples = await api("/api/samples");
  $("sample-procedures").innerHTML = "";
  for (const s of samples) {
    const b = document.createElement("button");
    b.className = "button";
    b.textContent = `Try the sample: ${s.task}`;
    b.onclick = () => loadProcedure(s.id).catch((e) => showProgress($("teach-progress"), e.message, true));
    $("sample-procedures").append(b);
  }
}

$("expert-file").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const job = await upload("/api/procedures", file, $("teach-progress"));
    await loadProcedure(job.procedure_id);
  } catch (err) {
    showProgress($("teach-progress"), err.message, true);
  }
  e.target.value = "";
};

$("attempt-file").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file || !procedure) return;
  try {
    const job = await upload(`/api/procedures/${procedure.id}/attempts`, file, $("check-progress"));
    await loadReport(job.attempt_id);
  } catch (err) {
    showProgress($("check-progress"), err.message, true);
  }
  e.target.value = "";
};

$("replay").onclick = replay;

(async () => {
  await loadSamples().catch(() => {});
  const q = new URLSearchParams(location.search);
  if (q.get("p")) {
    try {
      await loadProcedure(q.get("p"));
      if (q.get("a")) await loadReport(q.get("a"));
    } catch (e) {
      showProgress($("teach-progress"), e.message, true);
    }
  }
})();
