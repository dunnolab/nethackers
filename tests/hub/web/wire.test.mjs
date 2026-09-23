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
 *   /recognition (single object: {keepers, breakthroughs}),
 *   /elites?scope=generalist (enveloped, program_id rows),
 *   /board?scope=<identity> (enveloped, program_id rows, no episodes),
 *   /programs (enveloped list), /programs/{id} (single), /programs/{id}/identities
 *   (enveloped), /hackers/random (enveloped).
 * It runs the page's boot() and asserts the reworked render:
 *   pass 1 (populated): the frontier with the AutoAscend floor painted into
 *     untouched cells (73 cells), the two
 *     recognition tables (5 rows each, independent [ --More-- ] paging), and the
 *     three click-through popups -- identity leaderboard (/board?scope=), a
 *     breakthrough submission (/programs/{id} + /identities), and a hacker's
 *     contributions (/programs?owner= + /identities).
 *   pass 2 (private tier): the frontier POPULATES from the verified
 *     side-tables, using a distinct canned fixture from pass 1's public one
 *     (so "painted the private numbers" can't be confused with "still
 *     showing a stale public fetch"); the tier toggle is ROUND-TRIPPED
 *     Public then back to Private, since curTier now defaults to "verified"
 *     and a single click would be a same-value no-op; recognition stays
 *     visible on the private tier too, and the frontier/keepers/
 *     breakthroughs tier switches are asserted INDEPENDENT of each other.
 *   pass 3 (every fetch rejects): friendly empty states, console clean.
 *   pass 5 (loading shimmer): with every fetch parked on a gate, the frontier
 *     grid + both fame tables are already in the FIRST paint -- real labels,
 *     churning NetHack glyphs in the cells whose values are still in flight --
 *     and every placeholder is gone once the data lands. Also covers the
 *     re-shimmer on an unfetched tier and the no-flash on a cached one.
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

// /board?scope=<identity> -> enveloped program rows. The program here is
// DELIBERATELY absent from PROGRAMS_INDEX: on prod the index holds one page of
// /programs while the board ranks programs from the whole history, so 22 of 33
// rank-1 elites missed it and rendered "source unavailable" / "date unknown".
// The row carries its own reference{repo,commit} + registered_at, so a board
// row is self-sufficient and the index is not consulted at all.
const IDENTITY_BOARD = { rows: [
  { rank: 1, program_id: "prog_not_in_index", owner: "dun", reference: REF_AAA,
    registered_at: "2026-08-27T09:30:00+00:00",
    mean_progression: 0.3, median_progression: 0.3, ascensions: 1, deepest: "Mines' End" },
] };

// /programs/{id}/identities -> enveloped per-identity frontier (has episodes)
const FRONTIER = { rows: TOUCHED.map((id) => ({ identity: id, progression: 0.2, episodes: 15 })) };

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

// A prolific hacker: far more registered programs than the popup's page holds.
// Reproduces the prod shape (vkurenkov: 237 programs, 50-row page) that made the
// popup print the PAGE LENGTH as "registered programs". `total` is a property of
// the whole filtered set; `rows` is just the page. null total => a hub too old to
// send one (the client must then fall back to the page length).
const OWNER_TOTAL = 237;
function ownerPrograms(owner, limit, total) {
  const n = total == null ? Math.min(limit, 3) : Math.min(limit, total);
  const rows = Array.from({ length: n }, (_, i) => ({
    id: `prog_${owner}_${i}`, owner,
    reference: { repo: `github.com/${owner}/bot`, commit: `${owner}cmt${String(i).padStart(3, "0")}` },
    registered_at: "2026-08-20T00:00:00+00:00",
  }));
  return total == null ? { owner, rows } : { owner, total, rows };
}

function router(path) {
  const [route, query] = path.split("?");
  const params = new URLSearchParams(query || "");
  if (route === "/stats") return { programs: 2, hackers: 2, ascensions: 0, verified_programs: 1, last_registered_at: "2026-08-27T09:30:00+00:00" };
  if (route === "/baseline") return BASELINE;
  if (route === "/objectives") return IDENTITIES.map((n) => ({ name: n, episodes: 15 }));
  if (route === "/recognition") return RECOGNITION;
  if (route === "/elites") return ELITES_ALL;
  if (route === "/hackers/random") return { n: Number(params.get("n")), rows: RANDOM_HACKERS };
  if (route === "/programs") {
    const owner = params.get("owner");
    if (owner) return ownerPrograms(owner, Number(params.get("limit") || 50), OWNER_TOTAL);
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

function makeDom(fetchImpl, errors, url = "https://hub.test/") {
  const vc = new VirtualConsole();
  // jsdom emits "Not implemented" notices for canvas getContext / media play; the
  // page guards those paths (if(!g) return), so they are jsdom limits, not page bugs.
  vc.on("jsdomError", (e) => { if (!/Not implemented/.test(e.message)) errors.push("jsdomError: " + e.message); });
  vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));
  return new JSDOM(html, {
    // A real origin, not the default about:blank: the page rewrites its own
    // path for hacker deep links, and history.pushState cannot resolve a
    // relative URL against about:blank.
    url,
    runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(window) {
      window.fetch = fetchImpl;
      // jsdom implements neither of these; the page's masthead/audio code touches
      // matchMedia at top level, so stub it (reduced-motion off) before parse.
      window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
      // jsdom's Element.scrollIntoView throws "not implemented"; the inline detail
      // section calls it on open, so stub it to a no-op (real browsers have it).
      window.Element.prototype.scrollIntoView = function () {};
    },
  });
}

async function pass1() {
  console.log("\n== pass 1: populated dashboard ==");
  const errors = [];
  const fetched = [];
  const dom = makeDom((p) => { fetched.push(p); return Promise.resolve({ ok: true, status: 200, json: async () => router(p) }); }, errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];

  // frontier: every per-identity cell has a value (program or floor), 73 total.
  const frCells = qa("#rolegrid td.vv:not(.hval)");
  const floorCells = qa("#rolegrid td.vv.floor:not(.hval)").length;
  const progCells = qa("#rolegrid td.vv:not(.hval):not(.floor):not(.empty)").length;
  ok(frCells.length === 73, "frontier renders 73 identity cells");
  ok(floorCells > 0 && progCells > 0, `frontier mixes program (${progCells}) and floor (${floorCells}) cells`);
  ok(/AutoAscend floor/.test(q("#gridnote").textContent), "gridnote mentions the AutoAscend floor");

  // Boot must not pull a page of the registry to index client-side: that page
  // can never cover every ranked program (503 registered vs a 100-row page on
  // prod), and the rows that missed it rendered placeholder source/date cells.
  ok(!fetched.some((p) => /^\/programs(\?|$)/.test(p) && !/[?&]owner=/.test(p)),
     "boot does not fetch an unowned page of /programs to index");

  // masthead marquee + sidebar freshness stamp read live from /stats
  const mq = q("#mq").textContent;
  ok(/2 programs registered/.test(mq), "marquee shows the live program count (2)");
  ok(/none has ascended/.test(mq), "marquee: 'none has ascended' when ascensions=0");
  ok(/27 Aug 2026/.test(q("#updated").textContent), "last-updated shows the formatted registered_at (UTC)");

  // sidebar "programs verified" row: painted from /stats' verified_programs
  ok(!q("#regVerif").hidden, "the verified row is shown when /stats reports a count");
  ok(q("#odoVerif").textContent === "00001", "the verified odometer shows the live count (1)");

  // Recognition tables start compact and expand independently in five-row pages.
  ok(qa("#recordholders tbody tr").length === 5, "frontier keepers initially shows the top 5");
  ok(qa("#breakthroughs tbody tr").length === 5, "breakthrough log initially shows the latest 5");
  q('[data-fame-more="keepers"]').click();
  ok(qa("#recordholders tbody tr").length === 6, "keepers More control reveals the next page");
  ok(qa("#breakthroughs tbody tr").length === 5, "keepers expansion does not alter breakthroughs");
  q('[data-fame-more="breakthroughs"]').click();
  ok(qa("#breakthroughs tbody tr").length === 7, "breakthroughs More control reveals the next page");

  // Detail popups are dynamic + STACKABLE: each open pushes a fresh .detailmodal on top.
  const top = () => [...document.querySelectorAll(".detailmodal")].pop();
  const nDetail = () => document.querySelectorAll(".detailmodal").length;

  // click-through 1: a frontier row opens a popup (/board?scope=)
  q("#rolegrid tr.frontierrow").click();
  await sleep(40);
  ok(nDetail() === 1, "clicking a frontier row opens a popup");
  const idBody = top().querySelector(".win__body");
  ok(/@dun/.test(idBody.textContent) && /Mines' End/.test(idBody.textContent), "identity leaderboard shows the program row (@dun, Mines' End)");
  ok(/github\.com\/dun\/bot/.test(idBody.innerHTML), "identity source cell resolves reference{repo} to a github link");
  // the bug: both cells came from a client-side index built from one page of
  // /programs, so a program older than that page rendered these placeholders
  ok(!/source unavailable/.test(idBody.textContent), "a program missing from the /programs page is NOT 'source unavailable'");
  ok(/27 Aug 2026/.test(idBody.textContent), "the registered cell reads the row's own registered_at");
  ok(!/date unknown/.test(idBody.textContent), "...and never falls back to 'date unknown'");
  // clicking an @owner INSIDE the popup stacks a SECOND popup on top
  if (top()) top().querySelector(".ownerlink").click();
  await sleep(40);
  ok(nDetail() === 2, "clicking @owner inside a popup stacks a second popup on top");
  ok(/^@dun/.test(top().querySelector(".win__title span").textContent.trim()), "the stacked popup is the hacker (@owner title)");
  top().querySelector(".x").click();
  ok(nDetail() === 1, "closing the top popup reveals the one beneath");
  top().querySelector(".x").click();
  ok(nDetail() === 0, "closing again dismisses the stack");

  // click-through 2: a breakthrough row opens a popup (/programs/{id} + /identities)
  q("#breakthroughs tbody tr").click();
  await sleep(40);
  ok(/breakthrough/i.test(top().querySelector(".win__title span").textContent), "breakthrough popup titled for the identity");
  ok(/frontier advance/i.test(top().querySelector(".win__body").textContent), "breakthrough submission shows the advance");
  top().querySelector(".x").click();

  // click-through 3: a keeper row opens the hacker popup (/programs?owner= + /identities)
  q("#recordholders tbody tr").click();
  await sleep(40);
  ok(/^@keeper/.test(top().querySelector(".win__title span").textContent.trim()), "keeper row opens the hacker popup titled just @username");
  const hkText = top().querySelector(".win__body").textContent.replace(/\s+/g, " ");
  ok(/registered programs/i.test(hkText), "hacker popup lists registered programs");
  // the bug: this printed 50 (the page length) for anyone with more than 50
  ok(/registered programs ?237\b/.test(hkText), "hacker popup counts from the envelope total (237), not the 50-row page");
  ok(!/registered programs ?50\b/.test(hkText), "hacker popup no longer reports the page length as the count");
  ok(/newest 50 of 237/.test(hkText), "the truncated table says which slice it is showing");
  ok(top().querySelectorAll(".identityboard tbody tr").length === 50, "the table itself still renders just the page (50 rows)");
  top().querySelector(".x").click();

  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

async function pass2() {
  console.log("\n== pass 2: private tier populates the frontier, recognition stays visible ==");
  const errors = [];
  // The private ("verified") tier needs its own canned data, distinct from
  // router()'s public fixtures -- otherwise this pass could not tell "the grid
  // painted the private tier's own numbers" apart from "the grid is still
  // showing a leftover public fetch". These shapes mirror what the page's own
  // fetch calls read -- that is what a frontend-only file can vouch for, not
  // a claim about the live API: /elites and /board enveloped ({rows:[...]})
  // with program_id + reference{repo,commit} rows; /baseline a bare
  // {owner, per_identity, overall} object.
  const PRIVATE_PER_IDENTITY = {};
  for (const id of IDENTITIES) PRIVATE_PER_IDENTITY[id] = { progression: 0.09, deepest: "Dlvl:6", episodes: 15 };
  // Strip the floor from EVERY Monk identity, not just mon-hum-neu-mal, so
  // the Monk role ends up with exactly one measured cell: a program leading
  // mon-hum-neu-mal with NO floor to compare against (mirrors fixtures.py's
  // IDENTITY_C -- a verified RESULT with no verified FLOOR); its other 5
  // identities are fully unmeasured, matching production (round 2's live
  // repro: Monk had exactly one cell with any data at all). This
  // single-cell-role shape is a regression guard for two separate bugs:
  //  - C1 (the lift-accumulator bug): with every OTHER identity floored,
  //    the floorless cell's raw score used to leak into the role header's
  //    numerator uncancelled, fabricating a positive "lift" -- see the
  //    dpos-class assertion below.
  //  - the header-marker bug (round 2): a role with a program leading but
  //    no floor anywhere in it has roleLift genuinely undefined (not the
  //    degenerate 0 a floor-only role gives) -- the header must show an em
  //    dash, not the 'aa' chip that means "sits at the floor" -- see the
  //    Monk-header assertion below.
  for (const v of ["hum-cha-fem", "hum-cha-mal", "hum-law-fem", "hum-law-mal", "hum-neu-fem", "hum-neu-mal"]) {
    delete PRIVATE_PER_IDENTITY["mon-" + v];
  }
  const PRIVATE_BASELINE = { owner: "autoascend", per_identity: PRIVATE_PER_IDENTITY, overall: 0.091 };
  // Shifted from slice(30,38) so mon-hum-neu-mal is the ONLY touched Monk
  // identity -- otherwise Monk's other genuinely-beaten cells would
  // contribute their own real lift and mask the bugs above. Still distinct
  // from router()'s public TOUCHED (0..10); still 8 identities (pri picks
  // up the 3 this displaces from mon).
  const PRIVATE_TOUCHED = IDENTITIES.slice(33, 41);
  const REF_PRIVATE = { repo: "github.com/riv/bot", commit: "priv0000abc" };
  const PRIVATE_ELITES = { rows: PRIVATE_TOUCHED.map((id, i) => ({
    rank: 1, identity: id, program_id: "prog_priv", owner: "riv", score: 0.4 + i * 0.01, reference: REF_PRIVATE,
  })) };
  const PRIVATE_BOARD = { rows: [
    { rank: 1, program_id: "prog_priv", owner: "riv", reference: REF_PRIVATE,
      registered_at: "2026-08-29T09:30:00+00:00",
      mean_progression: 0.42, median_progression: 0.42, ascensions: 0, deepest: "Sokoban" },
  ] };
  // Distinct from RECOGNITION (router()'s default, used for the "verified"
  // tier below) so a bug that dropped the ?tier= param, or reused the cached
  // private rows for every tier, would show up as wrong row content -- not
  // just a caption, which is computed from local state (`keepersTier`)
  // rather than from the response body either way.
  const RECOGNITION_PUBLIC = {
    generated_at: "2026-08-27T09:30:00+00:00",
    keepers: [{ owner: "pubkeeper1", records: 3, identities: [TOUCHED[0]], roles: ["arc"], total_lift: 0.5 }],
    breakthroughs: [{ owner: "pubbreaker1", identity: TOUCHED[0], gain: 0.1, score: 0.2, previous: 0.1,
      program_id: "prog_aaa", reference: REF_AAA, at: "2026-08-20T09:30:00+00:00" }],
  };
  const fetchImpl = (p) => Promise.resolve({ ok: true, status: 200, json: async () => {
    const route = p.split("?")[0];
    const tier = new URLSearchParams(p.split("?")[1] || "").get("tier");
    if (tier === "verified") {
      if (route === "/elites") return PRIVATE_ELITES;
      if (route === "/baseline") return PRIVATE_BASELINE;
      if (route === "/board") return PRIVATE_BOARD;
    }
    if (route === "/recognition" && tier === "self-reported") return RECOGNITION_PUBLIC;
    return router(p);
  } });
  const dom = makeDom(fetchImpl, errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];

  // B2 (permanent, controller ruling): TIERS is the single source of truth for
  // the toggle buttons' text -- the markup's own text is only a pre-JS
  // fallback, overwritten on parse. Assert the overwrite actually happened, so
  // a refactor can't silently turn it into a no-op.
  const frontierBtns = qa("[data-tier-group='frontier']");
  const publicBtn = frontierBtns.find((b) => b.dataset.tier === "self-reported");
  const verifiedBtn = frontierBtns.find((b) => b.dataset.tier === "verified");
  ok(publicBtn && publicBtn.textContent === "Public Dungeons (15)", "public frontier toggle reads its TIERS label exactly");
  ok(verifiedBtn && verifiedBtn.textContent === "Private Dungeons (15)", "private frontier toggle reads its TIERS label exactly");

  // Tier round trip (controller ruling, not the B2 above): curTier now
  // DEFAULTS to "verified", so a single verifiedBtn.click() here would
  // re-press an already-pressed button -- a same-value no-op that exercises
  // no transition at all. Concretely: if the click handler's
  // `curTier=b.dataset.tier` (index.html) were mis-refactored to a hardcoded
  // `curTier="verified"`, that click would still "pass". Round-trip it
  // instead -- Public first (a genuine change away from the boot default),
  // then back to Private -- and check the grid follows a fixture value
  // (the AutoAscend baseline: 6.8% public vs 9.1% private) rather than
  // something the page could satisfy from local state alone.
  publicBtn.click();
  await sleep(60);
  ok(publicBtn.getAttribute("aria-pressed") === "true" && verifiedBtn.getAttribute("aria-pressed") === "false",
     "clicking Public presses the public frontier button and releases Private");
  ok(/6\.8%/.test(q("#gridnote").textContent), "gridnote's AutoAscend overall switches to the public baseline (6.8%)");
  ok(q("#tierhelp-frontier").dataset.k === "dungeons-public", "frontier ? marker points at the public tier's explanation");
  const publicProgCells = qa("#rolegrid td.vv:not(.hval):not(.floor):not(.empty)").length;
  ok(publicProgCells > 0, `public tier paints program cells (${publicProgCells}), not a blanked grid`);

  verifiedBtn.click();
  await sleep(60);
  ok(verifiedBtn.getAttribute("aria-pressed") === "true" && publicBtn.getAttribute("aria-pressed") === "false",
     "clicking Private re-presses the private frontier button and releases Public");
  ok(/9\.1%/.test(q("#gridnote").textContent), "gridnote's AutoAscend overall returns to the private baseline (9.1%) -- a real transition, not a same-value no-op");
  ok(q("#tierhelp-frontier").dataset.k === "dungeons-private", "frontier ? marker returns to the private tier's explanation");
  ok(qa("#recordholders tbody tr").length >= 5, "recognition keepers stay visible on the private tier");
  ok(qa("#breakthroughs tbody tr").length >= 5, "recognition breakthroughs stay visible on the private tier");
  const progCells = qa("#rolegrid td.vv:not(.hval):not(.floor):not(.empty)").length;
  ok(progCells > 0, `private tier paints program cells (${progCells}), not a blanked grid`);
  ok(/not yet measured/.test(q("#gridnote").textContent), "the grid note states private coverage honestly");

  // C1 regression guard: mon-hum-neu-mal has a private RESULT but no private
  // FLOOR (deleted from PRIVATE_PER_IDENTITY above) -- datum() correctly
  // reports aa:null for it, so its own row's delta must be an undefined em
  // dash, never a number computed against an implicit 0.0. The bug summed
  // AutoAscend only over cells that HAD a floor while averaging lift over
  // every SHOWN cell, so this cell's raw score leaked into the numerator
  // with nothing subtracted -- inflating the Monk role header into a false
  // positive "lift" it never earned. This is the regression guard for that
  // bug: it fails against the pre-fix accumulator (tSum/tAA/tN), which would
  // show the Monk header's delta cell as class="dpos" (green, "+6.7%") here.
  const monkRow = q('#rolegrid tr.frontierrow[data-identity="mon-hum-neu-mal"]');
  ok(monkRow.querySelector("td.vv").textContent.trim() === "40.0%",
     "mon-hum-neu-mal shows its real private-tier score (40.0%)");
  const monkRowDelta = monkRow.querySelector("td.dcol");
  ok(monkRowDelta.textContent.trim() === "\u2014",
     `mon-hum-neu-mal's own delta cell reads an em dash, not a fabricated number (got "${monkRowDelta.textContent.trim()}")`);
  const monkHeadDelta = monkRow.closest("table.fr").querySelector("tr.frhead td.dcol");
  ok(!monkHeadDelta.classList.contains("dpos"),
     `Monk's role header must not show a positive lift fabricated from mon-hum-neu-mal's unfloored score (class="${monkHeadDelta.className}", text="${monkHeadDelta.textContent.trim()}")`);

  // Header-marker regression guard (round 2): 'aa' means "sits at the
  // AutoAscend floor" and an em dash means "no floor to compare against" --
  // renderFrontier's fallback used to hand these out backwards for two role
  // shapes. Monk (constructed above with zero floors anywhere in the role)
  // is program-led but has nothing to measure against: it must show the em
  // dash, not 'aa' (which would falsely claim the role sits at the floor).
  ok(monkHeadDelta.textContent.trim() === "\u2014" && !monkHeadDelta.querySelector(".aachip"),
     `Monk's role header (program-led, no floor anywhere in the role) must show an em dash, not the 'aa' floor chip (class="${monkHeadDelta.className}", text="${monkHeadDelta.textContent.trim()}")`);
  // kni (Knight) has no touched identity at all -- both its cells sit at
  // the blanket 0.09 floor, so roleLift is the degenerate 0 those floor
  // cells contribute. It must show 'aa' (a real role average, but nothing
  // beats AutoAscend anywhere in it), never a fake-precise "+0.0%".
  const kniHeadDelta = q('#rolegrid tr.frontierrow[data-identity="kni-hum-law-fem"]').closest("table.fr").querySelector("tr.frhead td.dcol");
  ok(!!kniHeadDelta.querySelector(".aachip"),
     `Knight's role header (every cell at the floor) must show the 'aa' chip, not a fabricated "+0.0%" (class="${kniHeadDelta.className}", text="${kniHeadDelta.textContent.trim()}")`);

  // The three dungeon switches are independent: flipping Keepers to Public
  // must not move Breakthroughs or the Frontier. RECOGNITION_PUBLIC (stubbed
  // above) is distinct from RECOGNITION, so this also confirms the tier
  // actually reached the fetch instead of reusing a cached/leftover response.
  q("[data-tier-group='keepers'][data-tier='self-reported']").click();
  await sleep(60);
  ok(/pubkeeper1/.test(q("#recordholders").textContent), "keepers table loads the distinct public-tier fixture, not a leftover private fetch");
  ok(/PUBLIC DUNGEONS/.test(q("#recordholders caption").textContent), "keepers caption switches to PUBLIC DUNGEONS");
  ok(/PRIVATE DUNGEONS/.test(q("#breakthroughs caption").textContent), "breakthroughs caption is untouched by the keepers switch: still PRIVATE DUNGEONS");
  ok(verifiedBtn.getAttribute("aria-pressed") === "true", "the frontier's Private button is still pressed after flipping Keepers alone");

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
  ok(/No hacker is above AutoAscend/i.test(q("#recordholders").textContent), "keepers show the empty recognition state offline");
  ok(/No breakthroughs above AutoAscend/i.test(q("#breakthroughs").textContent), "breakthroughs show the empty recognition state offline");
  // honesty: /stats failed -> the marquee omits the count line and the freshness stamp stays a neutral dash
  ok(!/programs registered/.test(q("#mq").textContent), "marquee omits the stats line when /stats fails");
  ok(q("#updated") && q("#updated").textContent.trim() === "—", "last-updated is a neutral dash offline");
  ok(q("#regVerif").hidden, "the verified row stays hidden when /stats fails");
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

async function pass4() {
  console.log("\n== pass 4: hub without envelope `total` -> count falls back to the page ==");
  const errors = [];
  const dom = makeDom((p) => Promise.resolve({ ok: true, status: 200, json: async () => {
    const [route, query] = p.split("?");
    const owner = new URLSearchParams(query || "").get("owner");
    // an older hub: same rows, no `total` key
    if (route === "/programs" && owner) return ownerPrograms(owner, 50, null);
    return router(p);
  } }), errors);
  const { document } = dom.window;
  await sleep(200);
  document.querySelector('[data-fame-more="keepers"]');
  document.querySelector("#recordholders tbody tr").click();
  await sleep(60);
  const body = [...document.querySelectorAll(".detailmodal")].pop().querySelector(".win__body");
  const text = body.textContent.replace(/\s+/g, " ");
  ok(/registered programs ?3\b/.test(text), "with no `total`, the count falls back to the page length (3)");
  ok(!/newest \d+ of/.test(text), "nothing claims truncation when the page is all there is");
  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}


async function pass5() {
  console.log("\n== pass 5: loading shimmer (skeleton -> real data) ==");
  const errors = [];
  // Every fetch parks on a gate we control, so the LOADING state is observable
  // instead of being a microtask wide. Re-armable, to cover the tier toggle too.
  let gate, release;
  const arm = () => { gate = new Promise((r) => { release = r; }); };
  arm();
  const dom = makeDom((p) => gate.then(() => ({ ok: true, status: 200, json: async () => router(p) })), errors);
  const { document } = dom.window;
  const q = (s) => document.querySelector(s), qa = (s) => [...document.querySelectorAll(s)];
  const glyphs = (sel) => qa(sel).map((e) => e.textContent).join("");

  // (a) FIRST PAINT -- we have not awaited once, so no fetch has resolved yet.
  ok(qa("#rolegrid table.fr").length === 13, "frontier skeleton is in the first paint: 13 role tables");
  ok(qa("#rolegrid tr:not(.frhead)").length === 73, "frontier skeleton draws all 73 identity rows before any data");
  ok(qa("#rolegrid td.vv .shim").length === 86 && qa("#rolegrid td.dcol .shim").length === 86,
     "frontier value + delta cells shimmer (73 identities + 13 role headers each)");

  // the whole point of shimmering CELLS only: the labels are page constants, so
  // they are real text from the first frame and must never churn.
  ok(qa("#rolegrid td.vn .shim").length === 0, "frontier identity/role labels are real text, never shimmered");
  ok(/Valkyrie/.test(q("#rolegrid").textContent) && /dwa-law-fem/.test(q("#rolegrid").textContent),
     "skeleton labels carry the real role + variant names");
  ok(qa("#rolegrid tr.frontierrow").length === 0, "skeleton rows are inert -- no leaderboard to open yet");

  ok(qa("#recordholders tbody tr").length === 5 && qa("#breakthroughs tbody tr").length === 5,
     "both fame tables show FAME_PAGE_SIZE (5) placeholder rows");
  ok(qa("#recordholders tbody td").length === qa("#recordholders tbody td .shim").length &&
     qa("#breakthroughs tbody td").length === qa("#breakthroughs tbody td .shim").length,
     "every fame placeholder cell shimmers -- the row COUNT is unknown, unlike the frontier's");
  ok(/PRIVATE DUNGEONS/.test(q("#recordholders caption").textContent) &&
     /hacker/.test(q("#recordholders thead").textContent),
     "fame skeleton keeps the real caption + column headers");
  ok(!/Loading frontier keepers/.test(document.body.textContent), "the pre-JS 'Loading...' block is replaced");
  ok(!qa("#recordholders .more, #breakthroughs .more").length, "no [ --More-- ] control on a skeleton");

  // 227 cells of random glyphs would be read out as noise, so they are hidden
  // from assistive tech and the three regions announce themselves as busy.
  ok(qa(".shim").every((e) => e.getAttribute("aria-hidden") === "true"), "placeholder glyphs are aria-hidden");
  ok(["#rolegrid", "#recordholders", "#breakthroughs"].every((sel) => q(sel).getAttribute("aria-busy") === "true"),
     "all three loading regions are marked aria-busy");

  // (b) the glyphs actually churn, and come from the avatar's NetHack pool
  const before = glyphs(".shim");
  await sleep(300);
  ok(glyphs(".shim") !== before, "the shared ticker re-rolls the placeholder glyphs");
  // A placeholder churns the KIND of value its cell will hold: percentages
  // flicker through percentages, names through NetHack glyphs.
  const POOL = new Set("@dfx&;:eFD)[(!?/=\"*$%.#|-<>^{}".split(""));
  const kind = (k) => qa(`.shim[data-t="${k}"]`).map((e) => e.textContent);
  ok(kind("text").length > 0 && kind("text").every((t) => [...t].every((c) => POOL.has(c))),
     "text/name cells churn NetHack glyphs, same pool as the avatar");
  ok(qa('#rolegrid .shim[data-t="pct"]').length === 86 && kind("pct").every((t) => /^\d\d\.\d%$/.test(t)),
     "frontier value cells churn well-formed percentages, not glyphs");
  ok(qa('#breakthroughs .shim[data-t="pct"]').length === 10, "the log's before/result columns churn percentages too");
  ok(kind("spct").length === 86 && kind("spct").every((t) => /^[+-]\d\.\d%$/.test(t)),
     "frontier delta cells churn SIGNED percentages");
  // lift and advance are gains by construction (both renders hardcode "+"), so a
  // placeholder that flickered negative would promise a value the column cannot hold
  ok(kind("pp").length > 0 && kind("pp").every((t) => /^\+\d\.\d pp$/.test(t)), "lift/advance cells churn positive pp only");
  ok(kind("date").length === 5 && kind("date").every((t) => /^\d\d [A-Z][a-z]{2} 2026, \d\d:\d\d UTC$/.test(t)),
     "the breakthrough log's date column churns well-formed dates");
  ok(kind("int").length > 0 && kind("int").every((t) => /^\d$/.test(t)), "count columns churn single digits");
  // a placeholder that changed LENGTH between frames would jitter the text under it
  const widths = (sel) => qa(sel).map((e) => e.textContent.length).join(",");
  const w1 = widths(".shim");
  await sleep(200);
  ok(widths(".shim") === w1, "every placeholder kind is fixed-width -- no jitter as it churns");
  // the shimmer must never wear the clothing of a real value
  ok(!qa("#rolegrid .shim").some((e) => /dpos|dneg|asc|floor/.test(e.closest("td").className)),
     "a churning number never carries the heat/delta styling a real standing earns");

  // (c) data lands -> every placeholder is gone, replaced by the real render
  release();
  await sleep(200);
  ok(qa(".shim").length === 0, "no placeholder survives the real render");
  ok(qa("#rolegrid td.vv:not(.hval)").length === 73, "frontier renders its 73 real cells after boot");
  ok(qa("#rolegrid tr.frontierrow").length === 73, "rows become clickable once there is a leaderboard behind them");
  ok(qa("#recordholders tbody tr").length === 5 && /@keeper1/.test(q("#recordholders").textContent),
     "fame tables show real rows");
  ok(dom.window.eval("SHIM_TIMER") === 0, "the ticker stops itself once the last placeholder is gone");
  ok(["#rolegrid", "#recordholders", "#breakthroughs"].every((sel) => !q(sel).hasAttribute("aria-busy")),
     "aria-busy is cleared once the real values are in -- never left asserting a finished load is pending");

  // shimmer -> real dissolves rather than pops, staggered so the table develops
  ok(["#rolegrid", "#recordholders", "#breakthroughs"].every((sel) => q(sel).classList.contains("settle")),
     "the regions that were shimmering animate their new values in");
  ok(/--d:\s*\d+ms/.test(q("#rolegrid table.fr").getAttribute("style") || ""),
     "real role tables carry the stagger offset the fade reads");
  ok(/--d:\s*\d+ms/.test(q("#recordholders tbody tr").getAttribute("style") || ""),
     "real fame rows carry the stagger offset too");

  // (d) an UNCACHED tier flips back to the skeleton; a cached one must not flash
  arm();
  [...document.querySelectorAll("[data-tier-group='frontier']")].find((b) => b.dataset.tier === "self-reported").click();
  await sleep(0);
  ok(qa("#rolegrid td.vv .shim").length === 86, "switching to an unfetched tier shimmers the values again");
  ok(qa("#rolegrid td.vn .shim").length === 0, "... and still only the values");
  release();
  await sleep(150);
  ok(qa(".shim").length === 0, "the new tier's data clears the shimmer");

  await sleep(950);                                  // let the previous flip's fade class expire
  [...document.querySelectorAll("[data-tier-group='frontier']")].find((b) => b.dataset.tier === "verified").click();
  ok(qa(".shim").length === 0, "returning to a CACHED tier does not flash a skeleton");
  ok(!q("#rolegrid").classList.contains("settle"), "...and does not animate values that were never shimmering");

  // the last tier click leaves a render pending on a cached fetch; let it land
  // before tearing the window down, so it is checked here rather than throwing
  // into whatever pass runs next
  await sleep(60);
  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

// ---- pass 6: the scratch card --------------------------------------------
// The chart is painted onto a canvas over the opening block's body text, so a
// drag covers the page's own words with the drawing. jsdom has no 2d context
// and no pointer, so what is checked here is the state machine and the guards;
// the compositing itself is verified by driving Chromium and WebKit.
async function pass6() {
  console.log("\n== pass 6: the scratch card ==");
  const errors = [];
  const dom = makeDom(() => Promise.reject(new Error("offline")), errors);
  const { document, window } = dom.window;
  await sleep(120);
  const q = (s) => document.querySelector(s);
  const key = (k, target) => (target || document).dispatchEvent(
    new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
  const type = (s, t) => [...s].forEach((c) => key(c, t));
  const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  const about = q("#about"), chart = q("#agichart"), btn = q("#agiBtn");
  ok(about && chart && btn, "the block, the chart and the button are all on the page");
  ok(chart.tagName === "CANVAS", "the chart is a canvas -- overlapping strokes have to union");
  ok(about.contains(chart), "the chart is positioned against the block it covers");
  ok(chart.getAttribute("role") === "img" && /is it AGI/i.test(chart.getAttribute("aria-label")),
     "the drawing carries a described alternative for screen readers");
  ok(!chart.classList.contains("on") && !about.classList.contains("scratching"),
     "at rest: nothing is armed");
  ok(btn.textContent.trim() === "solving = AGI?" && btn.getAttribute("aria-pressed") === "false",
     "at rest: the button asks the question");

  click(btn);
  ok(chart.classList.contains("on") && about.classList.contains("scratching"), "the button arms it");
  ok(btn.textContent.trim() === "put it back" && btn.getAttribute("aria-pressed") === "true",
     "...and becomes the way back out");
  ok(q("#about .lead"), "the real text is untouched in the document -- it is covered, not removed");

  click(btn);
  ok(!chart.classList.contains("on") && !about.classList.contains("scratching"),
     "the button is a toggle and puts the page back");
  ok(btn.textContent.trim() === "solving = AGI?", "...and asks the question again");

  // the extended-command prompt is the other way in
  ok(q("#xcmd").hidden, "at rest: the prompt is hidden");
  key("#"); ok(!q("#xcmd").hidden, "# opens the extended-command prompt");
  type("pray"); key("Enter");
  ok(/^#pray: unknown extended command\.$/.test(q("#xcmd .xc-msg").textContent),
     "#pray is refused in NetHack's own words");
  ok(!about.classList.contains("scratching"), "a refused command arms nothing");
  await sleep(1800);
  ok(q("#xcmd").hidden, "the prompt closes itself after the refusal");

  const probe = document.createElement("input");
  document.body.appendChild(probe); probe.focus();
  key("#", probe);
  ok(q("#xcmd").hidden, "# typed into a form field is ignored");
  probe.remove();

  key("#"); type("agi"); key("Enter");
  ok(about.classList.contains("scratching"), "#agi arms the card too");
  key("Escape");
  ok(!about.classList.contains("scratching"), "escape puts the page back");

  // the tuning panel is a dev tool: it must never appear for a plain visitor
  ok(!q("#agitune"), "no ?tune=1 -> the tuning panel is not built at all");
  const t = window.__egg.tune;
  ok(t && typeof t.r === "number" && typeof t.soft === "number" && typeof t.paper === "string",
     "the scratch parameters are exposed as live values");

  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

async function pass7() {
  console.log("\n== pass 7: hacker deep links (/h/<username>) ==");
  const errors = [];
  const dom = makeDom(
    (p) => Promise.resolve({ ok: true, status: 200, json: async () => router(p) }),
    errors, "https://hub.test/h/keeper1");
  const { document, history, location } = dom.window;
  await sleep(250);
  const q = (s) => document.querySelector(s);
  const modals = () => [...document.querySelectorAll(".detailmodal")];
  const top = () => modals().pop();
  const title = () => (top() ? top().querySelector(".win__title span").textContent.trim() : "");
  const bodyText = () => (top() ? top().querySelector(".win__body").textContent : "");

  // 1. the shared link opens the popup by itself, on the entry it arrived on
  ok(modals().length === 1, "/h/keeper1 opens the hacker popup on load");
  ok(title() === "@keeper1", "...for the handle in the path");
  ok(/registered programs/i.test(bodyText()), "...and it is the real popup, not an empty shell");
  ok(location.pathname === "/h/keeper1", "a deep link does not push a duplicate entry");
  ok(document.title === "@keeper1 \u2014 NetHackers", "the tab says whose page this is");

  // 2. closing a deep-linked popup walks the path back to the front page
  if (top()) top().querySelector(".x").click();
  await sleep(20);
  ok(modals().length === 0, "closing the deep-linked popup dismisses it");
  ok(location.pathname === "/", "...and the path returns to the front page");
  ok(document.title === "NetHackers", "...and the tab goes back to the site title");

  // 3. opening one by click writes the shareable path
  document.querySelectorAll("#recordholders tbody tr")[1].click();
  await sleep(40);
  ok(title() === "@keeper2", "clicking a keeper row opens that hacker");
  ok(location.pathname === "/h/keeper2", "...and the URL becomes its deep link");
  ok(document.title === "@keeper2 \u2014 NetHackers", "...and the tab follows it");

  // 4. Back closes it; Forward brings it back -- the popup IS the history entry
  history.back();
  await sleep(40);
  ok(modals().length === 0 && location.pathname === "/", "Back closes the popup");
  history.forward();
  await sleep(60);
  ok(location.pathname === "/h/keeper2", "Forward returns to the hacker path");
  ok(modals().length === 1 && title() === "@keeper2", "...and reopens that popup");

  // 5. Escape closes through the same door the x does
  dom.window.document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await sleep(40);
  ok(modals().length === 0 && location.pathname === "/", "Escape closes and restores the path");

  // 6. stacking: only the hacker layer owns the URL. An identity popup leaves
  //    the path alone; the hacker stacked on top of it deep-links, and Back
  //    peels just that layer off.
  q("#rolegrid tr.frontierrow").click();
  await sleep(40);
  ok(modals().length === 1 && location.pathname === "/", "an identity popup does not touch the URL");
  top().querySelector(".ownerlink").click();
  await sleep(40);
  ok(modals().length === 2 && location.pathname === "/h/dun", "a hacker stacked on it still deep-links");
  history.back();
  await sleep(40);
  ok(modals().length === 1 && /^Frontier:/.test(title()),
     "Back peels off the hacker and leaves the identity popup beneath");
  ok(location.pathname === "/", "...and the path is the front page again");

  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

async function pass8() {
  console.log("\n== pass 8: hub with no verifier configured ==");
  const errors = [];
  const noVerifier = (p) => {
    const body = router(p);
    if (p.split("?")[0] === "/stats") { const { verified_programs, ...rest } = body; return rest; }
    return body;
  };
  const dom = makeDom((p) => Promise.resolve({ ok: true, status: 200, json: async () => noVerifier(p) }), errors);
  const { document } = dom.window;
  await sleep(200);
  const q = (s) => document.querySelector(s);
  // honesty: "no verifier to read the tier through" is not "zero verified".
  ok(q("#regVerif").hidden, "no verified_programs key -> the row is hidden, not a 00000");
  ok(/2 programs registered/.test(q("#mq").textContent), "the rest of the sidebar still reads live");
  ok(errors.length === 0, "no console/jsdom errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  dom.window.close();
}

await pass1();
await pass2();
await pass3();
await pass4();
await pass5();
await pass6();
await pass7();
await pass8();
checkDictvizRandomWiring();
console.log("\n" + (failures === 0 ? "ALL PASSED" : failures + " CHECK(S) FAILED"));
process.exit(failures === 0 ? 0 : 1);
