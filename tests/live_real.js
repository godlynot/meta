// Live mode on a real recorded attempt played in as the webcam.
const { chromium } = require("playwright");
const [BASE, Y4M, PID, SECONDS] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-video-capture=${Y4M}`] });
  const page = await (await browser.newContext({ permissions: ["camera"] })).newPage();
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${BASE}/?p=${PID}`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    window.__spoken = []; window.__trips = [];
    window.speechSynthesis.speak = (u) => { if (u.text) window.__spoken.push([((performance.now() - window.__t0) / 1000).toFixed(1), u.text]); setTimeout(() => u.onend && u.onend(), 200); };
    const f = window.fetch; window.fetch = async (u, o) => { const t = performance.now(); const r = await f(u, o);
      if (/\/clips$/.test(String(u))) window.__trips.push(Math.round(performance.now() - t)); return r; };
  });
  await page.evaluate(() => { window.__t0 = performance.now(); });
  await page.click("#live-btn");
  await page.waitForTimeout(+SECONDS * 1000);
  await page.click("#live-stop");
  await page.waitForFunction(() => /Session over/.test(document.getElementById("live-status").textContent), null, { timeout: 90000 }).catch(() => {});
  const out = await page.evaluate(() => ({
    status: document.getElementById("live-status").textContent,
    steps: [...document.querySelectorAll("#live-steps li")].map((li) => li.querySelector(".what").textContent.trim().replace(/\s+/g, " ")),
    spoken: window.__spoken, trips: window.__trips,
  }));
  console.log("STATUS:", out.status);
  console.log("SPOKEN (seconds after start):"); out.spoken.forEach(([t, s]) => console.log(`  ${t}s  ${s}`));
  console.log("FINAL CHECKLIST:"); out.steps.forEach((s) => console.log("  " + s));
  console.log("clip round trips (ms):", out.trips.join(", "));
  const reported = await page.waitForSelector("#report:not([hidden])", { timeout: 240000 }).then(() => true).catch(() => false);
  if (reported) {
    console.log("FULL REVIEW:", await page.textContent("#score-main"), (await page.textContent("#score")).trim(), "|", await page.textContent("#score-sub"));
    for (const t of await page.$$eval(".issue h4", (e) => e.map((x) => x.textContent))) console.log("  issue:", t);
  } else console.log("FULL REVIEW: did not appear", await page.textContent("#check-progress"));
  console.log("page errors:", errors.length ? errors : "none");
  await browser.close();
})();
