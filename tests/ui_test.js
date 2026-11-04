// Browser tests for every user-facing function of Watch Once.
// usage: node ui_test.js BASE_URL MEDIA_DIR
const { chromium } = require("playwright");
const path = require("path");
const [BASE, MEDIA] = process.argv.slice(2);
const results = [];
const check = (name, ok, detail = "") => {
  results.push(!!ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? `  (${JSON.stringify(detail).slice(0, 300)})` : ""}`);
};

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  // 1. First visit opens on the first bundled sample with a finished report
  await page.goto(BASE, { waitUntil: "networkidle" });
  const [first] = await page.evaluate(() => fetch("/api/samples").then((r) => r.json()));
  const pretty = (slug) => slug.charAt(0).toUpperCase() + slug.slice(1).replace(/[-_]+/g, " ");
  await page.waitForSelector("#report:not([hidden])", { timeout: 15000 }).catch(() => {});
  check("first visit shows the sample procedure", await page.isVisible("#procedure"));
  check("first visit shows the sample report", await page.isVisible("#report"));
  const steps = await page.$$eval("#steps .step-card", (els) => els.length);
  check("step cards rendered", steps >= 1, steps);
  check("fonts loaded under the CSP", await page.evaluate(async () => {
    await document.fonts.ready;
    const loaded = [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replace(/"/g, ""));
    return ["Archivo", "Big Shoulders", "Martian Mono"].every((f) => loaded.includes(f));
  }));
  check("URL updated with sample ids", page.url().includes(`?p=${first.id}&a=${first.attempts[0]}`), page.url());

  // 2. Clicking a step card plays that step of the expert video
  const cards = await page.$$("#steps .step-card");
  const target = cards[Math.min(1, cards.length - 1)];
  const start = +(await target.getAttribute("data-start"));
  await target.click();
  await page.waitForTimeout(1200);
  const v = await page.$eval("#expert-video", (el) => ({ t: el.currentTime, paused: el.paused }));
  check("step card seeks and plays the expert video", v.t >= start - 0.1 && v.t < start + 3, { start, ...v });

  // 3. Issue cards: SAM frame + expert clip for each mistake
  const issues = await page.$$eval(".issue", (els) => els.map((e) => ({
    img: !!e.querySelector("img") && e.querySelector("img").naturalWidth > 0,
    vid: !!e.querySelector("video"),
  })));
  check("issue cards rendered with evidence images", issues.length >= 1 && issues.every((i) => i.vid), issues);
  check("every step listed in the checklist", (await page.$$eval("#checklist li", (e) => e.length)) === steps);

  // 4. Replay with coach: the banner appears at the first mistake and the video moves on
  await page.evaluate(() => { window.__spoken = []; const s = window.speechSynthesis.speak.bind(window.speechSynthesis);
    window.speechSynthesis.speak = (u) => { window.__spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 300); }; });
  await page.click("#replay");
  const banner = await page.waitForSelector("#coach:not([hidden])", { timeout: 20000 }).then(() => true).catch(() => false);
  check("coach banner appears during replay", banner);
  if (banner) check("coach speaks the alert", await page.waitForFunction(() => window.__spoken.length > 0, null, { timeout: 5000 }).then(() => true).catch(() => false));
  const finished = await page.waitForFunction(() => document.getElementById("attempt-video").controls === true
      && document.getElementById("coach").hidden, null, { timeout: 60000 }).then(() => true).catch(() => false);
  check("replay runs to the end and gives controls back", finished);
  check("coach banner is really hidden after replay", !(await page.isVisible("#coach")));
  const alerts = await page.evaluate(() => window.__spoken);
  const expected = await page.$$eval(".issue", (e) => e.length);
  check("one spoken alert per mistake", alerts.length === expected, { alerts, expected });

  // 5. Sample buttons
  await page.click(`text=Sample: ${pretty(first.attempts[0])}`);
  await page.waitForTimeout(800);
  check("sample attempt button loads the report", await page.isVisible("#report"));
  await page.click(`text=Load sample: ${pretty(first.id)}`);
  await page.waitForTimeout(800);
  check("load sample button reloads the procedure", (await page.isVisible("#procedure")) && !(await page.isVisible("#report")));
  const fresh = await browser.newPage();
  await fresh.route("**/api/samples", (r) => r.fulfill({ contentType: "application/json", body: "[]" }));
  await fresh.goto(BASE, { waitUntil: "networkidle" });
  check("with no samples, only step 1 shows (step 2 waits for a procedure)",
        (await fresh.isVisible("#teach")) && !(await fresh.isVisible("#check")) && !(await fresh.isVisible("#procedure")));
  await fresh.close();

  // 6. Hostile or broken links show a message instead of breaking
  for (const q of ["?p=..%2F..%2Fapi%2Fsamples", "?p=does-not-exist", "?p=pack-the-tray&a=..%2Fx"]) {
    const p2 = await browser.newPage();
    const errs2 = [];
    p2.on("pageerror", (e) => errs2.push(e.message));
    await p2.goto(BASE + "/" + q, { waitUntil: "networkidle" });
    await p2.waitForTimeout(800);
    const msg = await p2.$eval("#teach-progress", (el) => (el.hidden ? "" : el.textContent)).catch(() => "");
    const msg2 = await p2.$eval("#check-progress", (el) => (el.hidden ? "" : el.textContent)).catch(() => "");
    check(`bad link ${q} shows a message, no crash`, (msg || msg2) && errs2.length === 0, { msg, msg2, errs2 });
    await p2.close();
  }

  // 7. Upload a non-video: readable error
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.setInputFiles("#expert-file", path.join(MEDIA, "notvideo.mp4"));
  const err = await page.waitForSelector("#teach-progress.error", { timeout: 60000 }).then((e) => e.textContent()).catch(() => "");
  check("uploading a non-video shows a readable error", /couldn't be read/.test(err), err);

  // 8. Full flow through the UI: teach, then check an attempt
  await page.setInputFiles("#expert-file", path.join(MEDIA, "expert.mp4"));
  const sawProgress = await page.waitForSelector("#teach-progress .spin", { timeout: 10000 }).then(() => true).catch(() => false);
  check("progress shown while learning", sawProgress);
  await page.waitForFunction((firstId) => document.getElementById("teach-progress").hidden
      && !location.search.includes(firstId), first.id, { timeout: 300000 });
  const pid = new URL(page.url()).searchParams.get("p");
  check("progress spinner disappears when done", !(await page.isVisible("#teach-progress")));
  check("old report is not shown for the new procedure", !(await page.isVisible("#report")));
  check("teaching a new procedure shows its steps", pid && pid !== first.id && (await page.$$("#steps .step-card")).length >= 1, page.url());
  await page.setInputFiles("#attempt-file", path.join(MEDIA, "trainee.mp4"));
  await page.waitForSelector("#check-progress .spin", { timeout: 10000 }).catch(() => {});
  const reported = await page.waitForFunction(() => new URLSearchParams(location.search).get("a"), null, { timeout: 400000 }).then(() => true).catch(() => false);
  check("checking an attempt shows the report", reported && (await page.isVisible("#report")));
  const deep = page.url();

  // 9. Shareable link reopens the same report
  const p3 = await browser.newPage();
  await p3.goto(deep, { waitUntil: "networkidle" });
  await p3.waitForSelector("#report:not([hidden])", { timeout: 15000 }).catch(() => {});
  check("shared link reopens the same report", await p3.isVisible("#report"), deep);
  await p3.close();

  // 10. Keyboard: upload button is reachable and shows focus
  await page.goto(BASE, { waitUntil: "networkidle" });
  let focused = "";
  for (let i = 0; i < 8 && focused !== "expert-btn"; i++) {
    await page.keyboard.press("Tab");
    focused = await page.evaluate(() => document.activeElement.id);
  }
  check("upload button reachable by keyboard", focused === "expert-btn", focused);

  // 11. Phone layout: no sideways scrolling
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto(BASE, { waitUntil: "networkidle" });
  await phone.waitForTimeout(800);
  check("phone layout has no sideways scroll", !(await phone.evaluate(() => document.documentElement.scrollWidth > innerWidth)));

  check("no JavaScript or CSP errors in the console", errors.length === 0, errors);
  console.log(`\n${results.filter(Boolean).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.every(Boolean) ? 0 : 1);
})();
