/* Headless wiring check for the reworked leaderboard in
 * src/nethackers/hub/web/index.html's live-data layer.
 *
 * This is a MANUAL dev-check, not part of the pytest CI (which is Python-only):
 *   node tests/hub/web/wire.test.mjs
 * It needs jsdom. If `require('jsdom')` fails, install it in a scratch dir and
 * point NODE_PATH at it, e.g.:
 *   (cd /tmp/js && npm i jsdom) && NODE_PATH=/tmp/js/node_modules node tests/hub/web/wire.test.mjs
 *
 * It stubs window.fetch with canned JSON matching the FastAPI endpoint shapes
 * (/board?scope=<identity|generalist|role> (enveloped, program_id rows),
 * /hackers?scope= (enveloped), /hackers/random (enveloped), /baseline,
 * /elites?scope=generalist (enveloped, program_id rows),
 * /programs/{id}/identities (enveloped), /progress, /objectives, /stats), runs the page's
 * boot(), and asserts the reworked render:
 *   pass 1 (generalist scope): grouped picker (87 options, 3 groups), the
 *     Hackers-primary union view (coverage+mean+Δ, NO firsts) + its AutoAscend
 *     floor row, the Programs aggregate view, and the frontier with the
 *     AutoAscend floor painted into untouched cells (73 cells total).
 *   pass 2 (identity + role scope): the identity board's deepest+score+Δ shape,
 *     and a role board's coverage+mean shape.
 *   pass 3 (every fetch rejects): friendly empty state, console clean.
 * /hackers/random's consumer sits behind a canvas getContext("2d") gate that jsdom
 * can't pass without the native `canvas` package (not installed here), so passes
 * 1-3 never reach it; a source-level check after them guards its .rows unwrap instead.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require("jsdom")); }
catch (e) {
  console.error("jsdom not importable. Install it and set NODE_PATH, e.g.:\n" +
    "  (cd /tmp/js && npm i jsdom) && NODE_PATH=/tmp/js/node_modules node tests/hub/web/wire.test.mjs");
  process.exit(2);
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML_PATH = resolve(__dirname, "../../../src/nethackers/hub/web/index.html");
const html = readFileSync(HTML_PATH, "utf8");

// the 73 identities, mirrored from the page's own role/variant table
const ROLE_VARS = {
 arc:["dwa-law-fem","dwa-law-mal","gno-neu-fem","gno-neu-mal","hum-law-fem","hum-law-mal","hum-neu-fem","hum-neu-mal"],
 bar:["hum-cha-fem","hum-cha-mal","hum-neu-fem","hum-neu-mal","orc-cha-fem","orc-cha-mal"],
 cav:["dwa-law-fem","dwa-law-mal","gno-neu-fem","gno-neu-mal","hum-law-fem","hum-law-mal","hum-neu-fem","hum-neu-mal"],
 hea:["gno-neu-fem","gno-neu-mal","hum-neu-fem","hum-neu-mal"],
 kni:["hum-law-fem","hum-law-mal"],
 mon:["hum-cha-fem","hum-cha-mal","hum-law-fem","hum-law-mal","hum-neu-fem","hum-neu-mal"],
 pri:["elf-cha-fem","elf-cha-mal","hum-cha-fem","hum-cha-mal","hum-law-fem","hum-law-mal","hum-neu-fem","hum-neu-mal"],
 ran:["elf-cha-fem","elf-cha-mal","gno-neu-fem","gno-neu-mal","hum-cha-fem","hum-cha-mal","hum-neu-fem","hum-neu-mal","orc-cha-fem","orc-cha-mal"],
 rog:["hum-cha-fem","hum-cha-mal","orc-cha-fem","orc-cha-mal"],
 sam:["hum-law-fem","hum-law-mal"],
 tou:["hum-neu-fem","hum-neu-mal"],
 val:["dwa-law-fem","hum-law-fem","hum-neu-fem"],
 wiz:["elf-cha-fem","elf-cha-mal","gno-neu-fem","gno-neu-mal","hum-cha-fem","hum-cha-mal","hum-neu-fem","hum-neu-mal","orc-cha-fem","orc-cha-mal"]};
const IDENTITIES = [];
for (const r of Object.keys(ROLE_VARS)) for (const v of ROLE_VARS[r]) IDENTITIES.push(r + "-" + v);

// per-identity baseline (all 73), so the frontier can paint a floor everywhere
const PER_IDENTITY = {};
for (const id of IDENTITIES) PER_IDENTITY[id] = { progression: 0.05, deepest: "Dlvl:3", episodes: 15 };
const BASELINE = { owner: "autoascend", per_identity: PER_IDENTITY, overall: 0.068 };

// a handful of identities a program has "touched" -> UNIVERSE (program cells); the rest stay floor
const TOUCHED = IDENTITIES.slice(0, 10);
const ELITES_ALL = TOUCHED.map((id, i) => ({
  rank: 1, identity: id, score: 0.2 + i * 0.01, owner: "dun", program_id: "prog_aaa",
}));

const GENERALIST_BOARD = [
  { rank: 1, program_id: "prog_aaa", owner: "dun", coverage: 20, identities_total: 73, mean_progression: 0.2, median_progression: 0.2, ascensions: 0, deepest: "Mines' End" },
  { rank: 2, program_id: "prog_bbb", owner: "ako", coverage: 8, identities_total: 73, mean_progression: 0.31, median_progression: 0.31, ascensions: 0, deepest: "Sokoban" },
];
const GENERALIST_HACKERS = [
  { rank: 1, owner: "dun", coverage: 24, identities_total: 73, mean_progression: 0.19 },
  { rank: 2, owner: "ako", coverage: 8, identities_total: 73, mean_progression: 0.31 },
];
const IDENTITY_BOARD = [
  { rank: 1, program_id: "prog_aaa", owner: "dun", coverage: 1, identities_total: 1, ascensions: 0, median_progression: 0.3, mean_progression: 0.3, deepest: "Mines' End" },
];
const IDENTITY_HACKERS = [{ rank: 1, owner: "dun", coverage: 1, identities_total: 1, mean_progression: 0.3 }];
const ROLE_BOARD = [
  { rank: 1, program_id: "prog_aaa", owner: "dun", coverage: 3, identities_total: 3, mean_progression: 0.24, median_progression: 0.24, ascensions: 0, deepest: "Mines' End" },
];
const ROLE_HACKERS = [{ rank: 1, owner: "dun", coverage: 3, identities_total: 3, mean_progression: 0.24 }];
const FRONTIER = TOUCHED.map((id) => ({ identity: id, progression: 0.2 }));
const RANDOM_HACKERS = ["dun", "ako", "sam"];

function router(path) {
  const [route, query] = path.split("?");
  const params = new URLSearchParams(query || "");
  const scope = params.get("scope");
  if (route === "/stats") return { programs: 2, hackers: 2, ascensions: 0, last_registered_at: "2026-08-27T09:30:00+00:00" };
  if (route === "/baseline") return BASELINE;
  if (route === "/objectives") return IDENTITIES.map((n) => ({ name: n, episodes: 15 }));
  if (route === "/elites") return { rows: ELITES_ALL };
  if (route === "/progress") return { series: [] };
  if (route.startsWith("/programs/") && route.endsWith("/identities")) return { rows: FRONTIER };
  if (route === "/hackers/random") return { n: Number(params.get("n")), rows: RANDOM_HACKERS };
  if (route === "/hackers") {
    if (scope === "generalist" || scope == null) return { rows: GENERALIST_HACKERS };
    if (scope === "val") return { rows: ROLE_HACKERS };
    return { rows: IDENTITY_HACKERS };
  }
  if (route === "/board") {
    if (scope === "generalist") return { rows: GENERALIST_BOARD };
    if (scope === "val") return { rows: ROLE_BOARD };
    return { rows: IDENTITY_BOARD }; // an identity token
  }
  throw new Error("unrouted " + path);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
function ok(cond, msg) { console.log((cond ? "  ok   " : "  FAIL ") + msg); if (!cond) failures++; }

function makeDom(fetchImpl, errors) {
  const vc = new VirtualConsole();
  // jsdom emits "Not implemented" notices for canvas getContext / media play; the
  // page guards those paths (if(!g) return), so they are jsdom limits, not page bugs.
  vc.on("jsdomError", (e) => { if (!/Not implemented/.test(e.message)) errors.push("jsdomError: " + e.message); });
  vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));
  return new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(window) {
      window.fetch = fetchImpl;
      // jsdom implements neither of these; the page's masthead/audio code touches
      // matchMedia at top level, so stub it (reduced-motion off) before parse.
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    },
  });
}

async function pass1() {
  console.log("\n== pass 1: generalist scope (populated) ==");
  const errors = [];
  const dom = makeDom((p) => Promise.resolve({ ok: true, status: 200, json: async () => router(p) }), errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];

  ok(qa("#objSelect option").length === 87, "picker has 87 options (1+13+73)");
  ok(qa("#objSelect optgroup").length === 3, "picker has 3 optgroups");
  ok(q("#objSelect").value === "generalist", "default objective is generalist");

  const pressed = qa("[data-view]").find((b) => b.getAttribute("aria-pressed") === "true");
  ok(pressed && pressed.dataset.view === "people", "Hackers is the default (primary) view");

  const cap = q("#scCap").textContent;
  ok(/GENERALIST/.test(cap) && /hackers/.test(cap), "caption: GENERALIST / hackers");
  const head = q("#scHead").textContent;
  ok(/coverage/.test(head) && /mean/.test(head) && /vs AA/.test(head), "hackers header: coverage + mean + Δ vs AA");
  ok(!/firsts/i.test(head), "hackers header has NO firsts");
  const rows = qa("#scBody tr");
  ok(rows.length === 3, "hackers: 2 rows + AutoAscend floor row = 3");
  ok(rows[0] && /@dun/.test(rows[0].textContent) && /24\/73/.test(rows[0].textContent), "hacker row1 @dun 24/73");
  ok(/autoascend/.test(rows[rows.length - 1].textContent), "last row is the autoascend floor");

  // switch to Programs
  q('[data-view="programs"]').click();
  await sleep(20);
  const phead = q("#scHead").textContent;
  ok(/program/.test(phead) && /coverage/.test(phead) && /vs AA/.test(phead), "programs (aggregate) header: coverage + Δ");
  ok(qa("#scBody tr").length === 3, "programs: 2 rows + floor = 3");

  // frontier: every per-identity cell has a value (program or floor), 73 total.
  // :not(.hval) excludes the per-role header average cells (also class "vv").
  const frCells = qa("#rolegrid td.vv:not(.hval)");
  const floorCells = qa("#rolegrid td.vv.floor:not(.hval)").length;
  const progCells = qa("#rolegrid td.vv:not(.hval):not(.floor):not(.empty)").length;
  ok(frCells.length === 73, "frontier renders 73 identity cells");
  ok(floorCells > 0 && progCells > 0, `frontier mixes program (${progCells}) and floor (${floorCells}) cells`);
  ok(/AutoAscend floor/.test(q("#gridnote").textContent), "gridnote mentions the AutoAscend floor");

  // masthead marquee + sidebar freshness stamp read live from /stats
  const mq = q("#mq").textContent;
  ok(/2 programs registered/.test(mq), "marquee shows the live program count (2)");
  ok(/none has ascended/.test(mq), "marquee: 'none has ascended' when ascensions=0");
  ok(!/3 programs registered/.test(mq), "marquee no longer hardcodes '3 programs'");
  ok(/27 Aug 2026/.test(q("#updated").textContent), "last-updated shows the formatted registered_at (UTC)");

  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

async function pass2() {
  console.log("\n== pass 2: identity + role scope ==");
  const errors = [];
  const dom = makeDom((p) => Promise.resolve({ ok: true, status: 200, json: async () => router(p) }), errors);
  const { document, Event } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];

  // to an identity, Programs view
  const sel = q("#objSelect"); sel.value = "val-dwa-law-fem"; sel.dispatchEvent(new Event("change"));
  q('[data-view="programs"]').click();
  await sleep(60);
  const head = q("#scHead").textContent;
  ok(/deepest reach/.test(head) && /score/.test(head) && /vs AA/.test(head), "identity programs header: deepest + score + Δ");
  ok(!/coverage/.test(head), "identity header has no coverage column");
  ok(/Mines' End/.test(q("#scBody").textContent), "identity program row shows real deepest (Mines' End)");

  // to a role
  sel.value = "val"; sel.dispatchEvent(new Event("change"));
  await sleep(60);
  const rhead = q("#scHead").textContent;
  ok(/coverage/.test(rhead) && /mean/.test(rhead), "role programs header: coverage + mean");
  ok(/3\/3/.test(q("#scBody").textContent), "role row shows coverage out of the role's identities (3/3)");

  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

async function pass3() {
  console.log("\n== pass 3: every fetch rejects (empty state, clean console) ==");
  const errors = [];
  const dom = makeDom(() => Promise.reject(new Error("offline")), errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s);
  ok(/be the first/i.test(q("#scBody").textContent), "empty hackers state invites the first register");
  ok(/autoascend/.test(q("#scBody").textContent), "floor row still present under the empty state");
  // honesty: with no baseline the floor must read "—", never a fabricated 0.000 (both views)
  q('[data-view="programs"]').click();
  await sleep(20);
  const floorRow = [...document.querySelectorAll("#scBody tr")].find((r) => /autoascend/.test(r.textContent));
  ok(floorRow && !/0\.000/.test(floorRow.textContent), "programs floor shows no fake 0.000 when baseline is absent");
  // honesty: /stats failed -> the marquee omits the count line and the freshness
  // stamp stays a neutral dash, never a stale/fabricated value
  ok(!/programs registered/.test(q("#mq").textContent), "marquee omits the stats line when /stats fails");
  ok(q("#updated") && q("#updated").textContent.trim() === "—", "last-updated is a neutral dash offline");
  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

function checkDictvizRandomWiring() {
  console.log("\n== dictviz source check: /hackers/random consumer ==");
  // Its fetch lives inside the audio-reactive wall's IIFE, gated on
  // `cv.getContext("2d")`. jsdom returns null there without the native `canvas`
  // package (not installed for this harness -- see the header comment), so that
  // IIFE returns early and pass1-3 above never reach the fetch. Guard the
  // envelope-unwrap at the source instead of behaviorally.
  const line = html.split("\n").find((l) => l.includes('jget("/hackers/random'));
  ok(line != null, "index.html fetches /hackers/random");
  ok(line != null && /\.rows/.test(line), "the /hackers/random read unwraps .rows before use");
  ok(line != null && !/\.then\(\s*buildRunners\s*\)/.test(line),
    "no longer hands the raw envelope straight to buildRunners");
}

await pass1();
await pass2();
await pass3();
checkDictvizRandomWiring();
console.log("\n" + (failures === 0 ? "ALL PASSED" : failures + " CHECK(S) FAILED"));
process.exit(failures === 0 ? 0 : 1);
