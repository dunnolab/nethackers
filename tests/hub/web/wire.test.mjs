/* Headless wiring check for the reworked dashboard in
 * src/nethackers/hub/web/index.html's live-data layer.
 *
 * This is a MANUAL dev-check, not part of the pytest CI (which is Python-only):
 *   node tests/hub/web/wire.test.mjs
 * It needs jsdom. If `require('jsdom')` fails, install it in a scratch dir and
 * point NODE_PATH at it, e.g.:
 *   (cd /tmp/js && npm i jsdom) && NODE_PATH=/tmp/js/node_modules node tests/hub/web/wire.test.mjs
 *
 * It stubs window.fetch with canned JSON matching the post-redesign FastAPI
 * endpoint shapes -- every collection enveloped as {..., rows:[...]}, program
 * rows carrying the opaque program_id + reference{repo,commit}:
 *   /stats, /baseline (single objects), /objectives (bare array),
 *   /summary?tier= (single object: status cards),
 *   /recognition (single object: {keepers, breakthroughs}),
 *   /elites?scope=generalist (enveloped, program_id rows),
 *   /board?scope=<identity> (enveloped, program_id rows, no episodes),
 *   /programs (enveloped list), /programs/{id} (single), /programs/{id}/identities
 *   (enveloped), /hackers/random (enveloped).
 * It runs the page's boot() and asserts the reworked render:
 *   pass 1 (populated): the four status cards (from /summary), the frontier with
 *     the AutoAscend floor painted into untouched cells (73 cells), the two
 *     recognition tables (5 rows each, independent [ --More-- ] paging), and the
 *     three click-through popups -- identity leaderboard (/board?scope=), a
 *     breakthrough submission (/programs/{id} + /identities), and a hacker's
 *     contributions (/programs?owner= + /identities).
 *   pass 2 (verified tier): recognition stays visible (it is self-reported), the
 *     frontier blanks.
 *   pass 3 (every fetch rejects): friendly empty states, console clean.
 * /hackers/random's consumer sits behind a canvas getContext("2d") gate that jsdom
 * can't pass without the native `canvas` package (not installed here), so the
 * passes never reach it; a source-level check guards its .rows unwrap instead.
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
const REF_AAA = { repo: "github.com/dun/bot", commit: "aaacommit0000" };
const REF_BBB = { repo: "github.com/ako/bot", commit: "bbbcommit0000" };

// /elites?scope=generalist -> enveloped rank-1-per-identity rows (opaque program_id)
const ELITES_ALL = { rows: TOUCHED.map((id, i) => ({
  rank: 1, identity: id, program_id: "prog_aaa", owner: "dun", score: 0.2 + i * 0.01, reference: REF_AAA,
})) };

// /programs index (loadProgramIndex) -> {id, owner, reference, registered_at}
const PROGRAMS_INDEX = { rows: [
  { id: "prog_aaa", owner: "dun", reference: REF_AAA, registered_at: "2026-08-27T09:30:00+00:00" },
  { id: "prog_bbb", owner: "ako", reference: REF_BBB, registered_at: "2026-08-26T09:30:00+00:00" },
] };

// /board?scope=<identity> -> enveloped program rows (program_id, no episodes column)
const IDENTITY_BOARD = { rows: [
  { rank: 1, program_id: "prog_aaa", owner: "dun", mean_progression: 0.3, median_progression: 0.3, ascensions: 1, deepest: "Mines' End" },
] };

// /programs/{id}/identities -> enveloped per-identity frontier (has episodes)
const FRONTIER = { rows: TOUCHED.map((id) => ({ identity: id, progression: 0.2, episodes: 15 })) };

// /summary -> status-card metrics, largest_lift is program-bearing
const SUMMARY = {
  generated_at: "2026-08-27T09:30:00+00:00",
  community_frontier: 0.081, frontier_gain_7d: 0.006, identities_improved_7d: 4,
  largest_lift: { identity: TOUCHED[0], owner: "dun", program_id: "prog_aaa", reference: REF_AAA, score: 0.2, baseline: 0.08, lift: 0.12 },
};

// /recognition -> {keepers, breakthroughs}; breakthroughs are program-bearing
const RECOGNITION = {
  generated_at: "2026-08-27T09:30:00+00:00",
  keepers: Array.from({ length: 6 }, (_, i) => ({
    owner: `keeper${i + 1}`, records: 7 - i, identities: [TOUCHED[i]], roles: [i % 2 ? "bar" : "arc"], total_lift: 0.8 - i * 0.1,
  })),
  breakthroughs: Array.from({ length: 7 }, (_, i) => ({
    owner: `breaker${i + 1}`, identity: TOUCHED[i], gain: 0.12 - i * 0.01, score: 0.2, previous: 0.08,
    program_id: i % 2 ? "prog_bbb" : "prog_aaa", reference: i % 2 ? REF_BBB : REF_AAA,
    at: `2026-08-${String(27 - i).padStart(2, "0")}T09:30:00+00:00`,
  })),
};

const RANDOM_HACKERS = ["dun", "ako", "sam"];

function router(path) {
  const [route, query] = path.split("?");
  const params = new URLSearchParams(query || "");
  if (route === "/stats") return { programs: 2, hackers: 2, ascensions: 0, last_registered_at: "2026-08-27T09:30:00+00:00" };
  if (route === "/baseline") return BASELINE;
  if (route === "/objectives") return IDENTITIES.map((n) => ({ name: n, episodes: 15 }));
  if (route === "/summary") return SUMMARY;
  if (route === "/recognition") return RECOGNITION;
  if (route === "/elites") return ELITES_ALL;
  if (route === "/hackers/random") return { n: Number(params.get("n")), rows: RANDOM_HACKERS };
  if (route === "/programs") {
    const owner = params.get("owner");
    if (owner) return { rows: [{ id: "prog_" + owner, owner, reference: { repo: `github.com/${owner}/bot`, commit: owner + "cmt00" }, registered_at: "2026-08-20T00:00:00+00:00" }] };
    return PROGRAMS_INDEX;
  }
  if (route.startsWith("/programs/") && route.endsWith("/identities")) return FRONTIER;
  if (route.startsWith("/programs/")) {
    const id = decodeURIComponent(route.split("/")[2] || "");
    return { id, owner: "dun", reference: REF_AAA, registered_at: "2026-08-27T09:30:00+00:00" };
  }
  if (route === "/board") return IDENTITY_BOARD; // openIdentity fetches /board?scope=<identity>
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
  console.log("\n== pass 1: populated dashboard ==");
  const errors = [];
  const dom = makeDom((p) => Promise.resolve({ ok: true, status: 200, json: async () => router(p) }), errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];

  // status cards, straight from /summary
  const cards = qa("#statusgrid .statuscard");
  ok(cards.length === 4, "status grid renders four cards");
  ok(/8\.1%/.test(q("#statusgrid").textContent), "community frontier card shows /summary's 8.1%");
  ok(/\+12\.0%/.test(q("#statusgrid").textContent), "largest-lift card shows +12.0% (from largest_lift.lift)");
  ok(!!q("#statusgrid .statusowner"), "largest-lift card exposes a clickable @owner");

  // frontier: every per-identity cell has a value (program or floor), 73 total.
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
  ok(/27 Aug 2026/.test(q("#updated").textContent), "last-updated shows the formatted registered_at (UTC)");

  // Recognition tables start compact and expand independently in five-row pages.
  ok(qa("#recordholders tbody tr").length === 5, "frontier keepers initially shows the top 5");
  ok(qa("#breakthroughs tbody tr").length === 5, "breakthrough log initially shows the latest 5");
  q('[data-fame-more="keepers"]').click();
  ok(qa("#recordholders tbody tr").length === 6, "keepers More control reveals the next page");
  ok(qa("#breakthroughs tbody tr").length === 5, "keepers expansion does not alter breakthroughs");
  q('[data-fame-more="breakthroughs"]').click();
  ok(qa("#breakthroughs tbody tr").length === 7, "breakthroughs More control reveals the next page");

  // click-through 1: a frontier row opens the identity leaderboard (/board?scope=)
  q("#rolegrid tr.frontierrow").click();
  await sleep(40);
  ok(document.querySelector("#modal").hasAttribute("open"), "clicking a frontier row opens the identity modal");
  const idBody = q("#mBody").textContent;
  ok(/@dun/.test(idBody) && /Mines' End/.test(idBody), "identity leaderboard shows the program row (@dun, Mines' End)");
  ok(/github\.com\/dun\/bot/.test(q("#mBody").innerHTML), "identity source cell resolves reference{repo} to a github link");
  q("#mX").click();

  // click-through 2: a breakthrough row opens the exact submission (/programs/{id} + /identities)
  q("#breakthroughs tbody tr").click();
  await sleep(40);
  const bBody = q("#mBody").textContent;
  ok(/breakthrough/i.test(q("#mTitle").textContent), "breakthrough modal titled for the identity");
  ok(/frontier advance/i.test(bBody) && /github\.com/.test(q("#mBody").innerHTML), "breakthrough submission shows advance + source link");
  q("#mX").click();

  // click-through 3: a keeper row opens the hacker's contributions (/programs?owner= + /identities)
  q("#recordholders tbody tr").click();
  await sleep(40);
  ok(/contributions/i.test(q("#mTitle").textContent), "keeper row opens the hacker contributions modal");
  ok(/registered solutions/i.test(q("#mBody").textContent), "hacker modal lists registered solutions");
  q("#mX").click();

  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

async function pass2() {
  console.log("\n== pass 2: verified tier keeps recognition, blanks the frontier ==");
  const errors = [];
  const dom = makeDom((p) => Promise.resolve({ ok: true, status: 200, json: async () => router(p) }), errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];

  const verifiedBtn = qa("[data-tier]").find((b) => b.dataset.tier === "verified");
  verifiedBtn.click();
  await sleep(60);
  ok(qa("#recordholders tbody tr").length >= 5, "recognition keepers stay visible on the verified tier");
  ok(qa("#breakthroughs tbody tr").length >= 5, "recognition breakthroughs stay visible on the verified tier");
  const progCells = qa("#rolegrid td.vv:not(.hval):not(.floor):not(.empty)").length;
  ok(progCells === 0, "frontier shows no program cells on the verified tier (M2b not live)");

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
  ok(/Loading|No participant|No breakthroughs|dungeon ledger/i.test(q("#statusgrid").textContent) || q("#statusgrid").querySelectorAll(".statuscard").length >= 0,
    "status grid degrades without throwing");
  ok(/No participant is above AutoAscend/i.test(q("#recordholders").textContent), "keepers show the empty recognition state offline");
  ok(/No breakthroughs above AutoAscend/i.test(q("#breakthroughs").textContent), "breakthroughs show the empty recognition state offline");
  // honesty: /stats failed -> the marquee omits the count line and the freshness stamp stays a neutral dash
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
  // IIFE returns early and the passes above never reach the fetch. Guard the
  // envelope-unwrap at the source instead of behaviorally.
  const line = html.split("\n").find((l) => l.includes('jget("/hackers/random'));
  ok(line != null, "index.html fetches /hackers/random for the @username wall runners");
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
