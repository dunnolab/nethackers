/* Real-browser check that the @username wall runners are clickable.
 *
 * The runners are canvas TEXT, not elements -- jsdom cannot reach them at all
 * (the wall bails at getContext("2d"), which jsdom returns null for), and there
 * is no DOM node a selector could target. So this check drives a real Chromium:
 * it asks the page where a runner currently is, clicks that spot, and asserts
 * the hacker popup opened and the URL became /h/<username> -- the same outcome
 * a click on a Hackers-table row produces.
 *
 * This is a MANUAL dev-check, not part of the pytest CI (which is Python-only):
 *   node tests/hub/web/runners.click.test.mjs
 * It needs playwright + a chromium build:
 *   npx playwright install chromium
 *
 * A stub HTTP server stands in for the hub: it serves index.html at / and at
 * /h/<name> (so a deep link reloads the page, as the real hub does) and canned
 * JSON for the endpoints the page boots. Only /hackers/random matters here --
 * the rest may even fail, since the page keeps friendly empty states either way
 * (wire.test.mjs pass 3 covers that) -- but they are stubbed so the console
 * stays clean and the popup body renders real rows.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createServer } from "node:http";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); }
catch (e) {
  try { ({ chromium } = require("playwright-core")); }
  catch (e2) {
    console.error("playwright not importable. Install it, e.g.:\n" +
      "  (cd /tmp/js && npm i playwright) && NODE_PATH=/tmp/js/node_modules node tests/hub/web/runners.click.test.mjs\n" +
      "  npx playwright install chromium");
    process.exit(2);
  }
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML_PATH = resolve(__dirname, "../../../src/nethackers/hub/web/index.html");
const html = readFileSync(HTML_PATH, "utf8");

/* ---------- assertions ---------- */
let pass = 0, fail = 0;
function ok(cond, label) {
  if (cond) { pass++; console.log("  ok   " + label); }
  else { fail++; console.log("  FAIL " + label); }
}
function eq(actual, expected, label) {
  ok(actual === expected, `${label} (got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)})`);
}

/* ---------- canned hub ---------- */
// A full wall (the page asks for 20), with long handles on purpose: a wide label
// is an easier target and proves the hit box tracks the whole handle, not just
// the @ glyph on the wall.
const RANDOM_HACKERS = ["dungeondelver", "amuletseeker", "sokobanwalker", "gnomishmines",
  "astralplane", "wandofdeath", "luckystone", "ringofcon", "magicmarker", "bagofholding",
  "scrollofmail", "cockatrice", "elberethfan", "gravedigger", "medusagaze", "sokoprize",
  "vaultguard", "wraithcorpse", "candelabrum", "blessedtins"];
const KEEPER = "frontierkeeper";        // the one Hackers-table row, for the side-by-side check
const PROGRAM = {
  program_id: "prog_abc123", owner: "dungeondelver", role: "valkyrie",
  reference: { repo: "dungeondelver/nethack-solver", commit: "a".repeat(40) },
  registered_at: "2026-09-01T00:00:00Z", mean_progression: 0.11, ascensions: 0,
};

function canned(route, params) {
  if (route === "/hackers/random") return { n: Number(params.get("n")), rows: RANDOM_HACKERS };
  if (route === "/stats") return { programs: 3, hackers: 3, verified_programs: 1, last_registered_at: PROGRAM.registered_at };
  if (route === "/objectives") return [];
  // one keeper, so the Hackers table has a row to compare the runners against
  if (route === "/recognition") return {
    keepers: [{ owner: KEEPER, records: 2, identities: ["val-hum-neu-mal"], roles: ["val"], total_lift: 0.04 }],
    breakthroughs: [],
  };
  if (route === "/baseline") return { per_identity: {}, progression: null };
  if (route === "/elites" || route === "/board" || route === "/programs") return { rows: [PROGRAM], total: 1 };
  if (route === "/poll") return { votes: [] };
  if (/^\/programs\/[^/]+\/identities$/.test(route)) return { rows: [], total: 0 };
  if (/^\/programs\/[^/]+$/.test(route)) return PROGRAM;
  return null;
}

function startStub() {
  const server = createServer((req, res) => {
    const u = new URL(req.url, "http://127.0.0.1");
    const route = u.pathname;
    // the page itself, at / and at every /h/<username> deep link
    if (route === "/" || /^\/h\/[^/]+$/.test(route)) {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
      return;
    }
    const body = canned(route, u.searchParams);
    if (body != null) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end("{}");
  });
  return new Promise((done) => server.listen(0, "127.0.0.1", () => done(server)));
}

/* ---------- helpers ---------- */
// The wall exposes its runners' on-screen boxes for exactly this harness (the
// same reason ?vizpreview exists): canvas text has no element to click. Each box
// comes back with the point this harness should aim at -- the centre of the part
// that is actually on-screen, since a long handle on the left wall can trail off
// the left edge -- and whether a real control sits on top of it there.
const boxes = (page) => page.evaluate(() => {
  const CTRL = "a,button,input,select,textarea,summary,label,[role=button],[role=radio],.hackerrow,.ownerlink";
  const rs = window.__wallRunnerBoxes ? window.__wallRunnerBoxes() : null;
  if (!rs) return null;
  return rs.map((r) => {
    const x0 = Math.max(0, r.box.x), x1 = Math.min(innerWidth, r.box.x + r.box.w);
    const y0 = Math.max(0, r.box.y), y1 = Math.min(innerHeight, r.box.y + r.box.h);
    const at = { x: Math.round((x0 + x1) / 2), y: Math.round((y0 + y1) / 2) };
    const el = document.elementFromPoint(at.x, at.y);
    return { ...r, at, blocked: !!(el && el.closest && el.closest(CTRL)) };
  });
});

// The runners ride the whole page perimeter, and the landing page is many
// viewports tall, so only a slice of them is ever drawn. Scan down until one is
// both on-screen and not sitting under a control of the page's own.
async function findRunner(page) {
  const vh = page.viewportSize().height;
  const tall = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let y = 0; y < tall; y += Math.round(vh * 0.8)) {
    await page.evaluate((v) => scrollTo(0, v), y);
    for (let i = 0; i < 6; i++) {
      const rs = await boxes(page);
      const hit = (rs || []).find((r) => !r.blocked);
      if (hit) return hit;
      await page.waitForTimeout(70);
    }
  }
  return null;
}
const fresh = async (page, name) => ((await boxes(page)) || []).find((r) => r.name === name);

async function run() {
  const server = await startStub();
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  // A tall viewport keeps several runners on-screen; the wall only draws a
  // runner whose perimeter cell is currently visible.
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  try {
    console.log("\n== the wall runners are clickable ==");
    await page.goto(base + "/", { waitUntil: "load" });

    const runner = await findRunner(page);
    ok(runner != null, "the wall reports a clickable runner box on screen");
    if (!runner) return;
    ok(RANDOM_HACKERS.includes(runner.name), `the box carries a real handle (${runner.name})`);
    ok(runner.box.w > 0 && runner.box.h >= 14,
      `the box is a tappable size (${Math.round(runner.box.w)}x${Math.round(runner.box.h)})`);

    // hover: the same affordance a Hackers-table row gives
    await page.mouse.move(runner.at.x, runner.at.y);
    await page.waitForTimeout(90);
    eq(await page.evaluate(() => getComputedStyle(document.body).cursor), "pointer",
      "hovering a runner shows the pointer cursor");
    eq(await page.evaluate(() => (window.__wallRunnerBoxes().find((r) => r.hot) || {}).name), runner.name,
      "the hovered runner is the one marked hot (it draws highlighted)");

    // move off it: the affordance clears
    await page.mouse.move(Math.round(page.viewportSize().width / 2), 2);
    await page.waitForTimeout(90);
    eq(await page.evaluate(() => getComputedStyle(document.body).cursor), "auto",
      "moving off a runner drops the pointer cursor");
    ok((await boxes(page)).every((r) => !r.hot), "and nothing stays highlighted");

    // click: the popup opens and the URL is the hacker's deep link
    const again = (await fresh(page, runner.name)) || runner;
    await page.mouse.click(again.at.x, again.at.y);
    await page.waitForSelector(".detailmodal[data-hacker]", { timeout: 4000 }).catch(() => {});
    eq(await page.evaluate(() => {
      const m = document.querySelector(".detailmodal[data-hacker]");
      return m ? m.dataset.hacker : null;
    }), again.name, "clicking a runner opens that hacker's popup");
    eq(await page.evaluate(() => {
      const t = document.querySelector(".detailmodal[data-hacker] .win__title span");
      return t ? t.textContent : null;
    }), "@" + again.name, "the popup is titled with the handle");
    eq(await page.evaluate(() => decodeURIComponent(location.pathname)), "/h/" + again.name,
      "the click pushed the hacker's deep link");
    eq(await page.evaluate(() => getComputedStyle(document.body).cursor), "auto",
      "the pointer cursor is released once the popup is up");

    // A second click in the same spot is a click on the popup's own backdrop: it
    // closes the popup (the page's existing behaviour) and must NOT reach the wall
    // and stack a second one on top.
    await page.mouse.click(again.at.x, again.at.y);
    await page.waitForTimeout(300);
    eq(await page.evaluate(() => document.querySelectorAll(".detailmodal").length), 0,
      "a click behind an open popup reaches the backdrop, not the wall");
    eq(await page.evaluate(() => location.pathname), "/", "and the backdrop close restores the landing URL");

    // Back closes a runner-opened popup too -- the deep-link contract
    const third = (await fresh(page, runner.name)) || again;
    await page.mouse.click(third.at.x, third.at.y);
    await page.waitForSelector(".detailmodal[data-hacker]", { timeout: 4000 }).catch(() => {});
    eq(await page.evaluate(() => document.querySelectorAll(".detailmodal[data-hacker]").length), 1,
      "the runner opens again after closing");
    await page.goBack();
    await page.waitForTimeout(300);
    eq(await page.evaluate(() => document.querySelectorAll(".detailmodal").length), 0,
      "Back closes the popup");
    eq(await page.evaluate(() => location.pathname), "/", "Back restores the landing URL");

    console.log("\n== the runners never steal a real control's click ==");
    // Lay a link exactly over a runner: the topmost element takes the click, so
    // the wall must stand down rather than hijack it.
    const overlaid = (await fresh(page, runner.name)) || runner;
    await page.evaluate((b) => {
      const a = document.createElement("a");
      a.id = "overlay-probe"; a.href = "#hackers"; a.textContent = "probe";
      a.style.cssText = `position:fixed;left:${b.box.x}px;top:${b.box.y}px;`
        + `width:${b.box.w}px;height:${b.box.h}px;z-index:99999;`;
      a.addEventListener("click", (e) => { e.preventDefault(); window.__probeClicked = true; });
      document.body.appendChild(a);
    }, overlaid);
    await page.mouse.click(overlaid.at.x, overlaid.at.y);
    await page.waitForTimeout(250);
    ok(await page.evaluate(() => window.__probeClicked === true), "the overlaid link got its click");
    eq(await page.evaluate(() => document.querySelectorAll(".detailmodal[data-hacker]").length), 0,
      "no hacker popup opened from under the link");
    await page.evaluate(() => document.getElementById("overlay-probe").remove());

    console.log("\n== the page's own clicks still work ==");
    // The wall canvas is fixed over the whole viewport. If it ever became
    // pointer-events:auto it would swallow every click on the page.
    eq(await page.evaluate(() => getComputedStyle(document.getElementById("dictviz")).pointerEvents),
      "none", "the wall canvas still lets clicks through");
    await page.click("#recordholders tbody tr", { timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(250);
    eq(await page.evaluate(() => {
      const m = document.querySelector(".detailmodal[data-hacker]");
      return m ? m.dataset.hacker : null;
    }), KEEPER, "a Frontier-keepers row still opens its popup");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(250);
    eq(await page.evaluate(() => document.querySelectorAll(".detailmodal").length), 0,
      "Escape closes it again");

    console.log("\n== bare margin ==");
    // a point in the left margin that no runner currently occupies
    const empty = await page.evaluate(() => {
      const bs = window.__wallRunnerBoxes();
      for (let y = 12; y < innerHeight - 12; y += 9) {
        if (!bs.some((r) => y >= r.box.y && y <= r.box.y + r.box.h)) return { x: 5, y };
      }
      return null;
    });
    ok(empty != null, "found a runner-free spot in the margin");
    if (empty) {
      await page.mouse.click(empty.x, empty.y);
      await page.waitForTimeout(250);
      eq(await page.evaluate(() => document.querySelectorAll(".detailmodal").length), 0,
        "clicking bare margin opens nothing");
    }

    ok(errors.length === 0, "no page errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  } finally {
    await browser.close();
    server.close();
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

run().catch((e) => { console.error(e); process.exit(1); });
