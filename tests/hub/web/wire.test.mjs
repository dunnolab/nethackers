/* Headless wiring check for src/nethackers/hub/web/index.html's live-data layer.
 *
 * This is a MANUAL dev-check, not part of the pytest CI (which is Python-only):
 *   node tests/hub/web/wire.test.mjs
 * It needs jsdom. If `require('jsdom')` fails, install it in a scratch dir and
 * point NODE_PATH at it, e.g.:
 *   (cd /tmp/js && npm i jsdom) && NODE_PATH=/tmp/js/node_modules node tests/hub/web/wire.test.mjs
 *
 * It stubs window.fetch with canned JSON matching the FastAPI endpoint shapes
 * (verified in task-7 against a fixture DB), runs the page's boot(), and asserts
 * the render state. Coverage:
 *   pass 1 (live snapshot, 3 @vkurenkov, best 0.167): counters, programs board +
 *     baseline, callouts, frontier UNIVERSE regime, chart, the lazy-frontier
 *     popup, the HACKERS (people) view aggregate, the PROGRAM (champion) frontier
 *     regime, and the verified tier.
 *   pass 2 (multi-owner): the Hackers view aggregation + ranking across owners.
 *   pass 3 (every fetch rejects): friendly empty state, console clean.
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

// --- canned payloads, pass 1: live snapshot (3 @vkurenkov on val-dwa-law-fem) ---
const D1 = "sha256:e11151f0000000000000000000000000000000000000000000000000000000a";
const D2 = "sha256:ebaa3920000000000000000000000000000000000000000000000000000000b";
const D3 = "sha256:be6ad2c0000000000000000000000000000000000000000000000000000000c";
const sol = (d, o) => ({ digest: d, repo: `github.com/${o}/nethacker`, commit_sha: "a".repeat(40), owner: o, root: ".", entrypoint: "bot.py", registered_at: "2026-08-23" });
const elite = (id, d, s, r, o) => ({ identity: id, solution_digest: d, score: s, rank: r, owner: o, repo: `github.com/${o}/nethacker`, commit_sha: "a".repeat(40), tier: "self-reported" });

const CANNED_LIVE = {
  "/stats": { programs: 3, hackers: 1, ascensions: 0, identities_touched: 1, best: 0.167 },
  "/baseline": { owner: "autoascend", per_identity: { "val-dwa-law-fem": { progression: 0.089, deepest: "Dlvl:3", episodes: 15 } }, overall: 0.089 },
  "/objectives": [
    { name: "random", kind: "random", aggregation: "asc_median_mean", episodes: 32 },
    { name: "val-dwa-law-fem", kind: "identity", aggregation: "mean", episodes: 15 },
  ],
  "/search": [sol(D1, "vkurenkov"), sol(D2, "vkurenkov"), sol(D3, "vkurenkov")],
  "/elites": [elite("val-dwa-law-fem", D1, 0.167, 1, "vkurenkov"), elite("val-dwa-law-fem", D2, 0.135, 2, "vkurenkov"), elite("val-dwa-law-fem", D3, 0.124, 3, "vkurenkov")],
  "/board/val-dwa-law-fem/self-reported": [
    { rank: 1, solution_digest: D1, owner: "vkurenkov", episodes: 15, ascensions: 0, median_progression: 0.179, mean_progression: 0.167 },
    { rank: 2, solution_digest: D2, owner: "vkurenkov", episodes: 15, ascensions: 0, median_progression: 0.075, mean_progression: 0.135 },
    { rank: 3, solution_digest: D3, owner: "vkurenkov", episodes: 15, ascensions: 0, median_progression: 0.075, mean_progression: 0.124 },
  ],
  "/board/val-dwa-law-fem/verified": [],
  // non-empty so the "program" (champion) frontier regime has something to render
  "/board/random/self-reported": [{ rank: 1, solution_digest: D1, owner: "vkurenkov", episodes: 15, ascensions: 0, median_progression: 0.167, mean_progression: 0.167 }],
  "/board/coverage": [{ rank: 1, solution_digest: D1, owner: "vkurenkov", cells_held: 32 }],
  "/board/firsts": [{ rank: 1, solution_digest: D3, owner: "vkurenkov", firsts: 28 }],
  "/progress/self-reported": { series: [
    { t: "2026-08-21", frontier: 0.124, ascensions: 0, coverage: 28 },
    { t: "2026-08-23", frontier: 0.167, ascensions: 0, coverage: 32 },
  ] },
  "/progress/verified": { series: [] },
  // champion (and popup) frontier: two identities, so the program regime lights
  // more cells than the single-identity UNIVERSE -> the two regimes are distinct
  "/frontier/e11151f": [{ identity: "val-dwa-law-fem", progression: 0.167, episodes: 15 }, { identity: "wiz-elf-cha-mal", progression: 0.09, episodes: 15 }],
};

// --- canned payloads, pass 2: two owners (alice x2, bob x1) for people ranking ---
const A1 = "sha256:aaa11110000000000000000000000000000000000000000000000000000000a";
const A2 = "sha256:aaa22220000000000000000000000000000000000000000000000000000000b";
const B1 = "sha256:bbb11110000000000000000000000000000000000000000000000000000000c";
const CANNED_MULTI = {
  "/stats": { programs: 3, hackers: 2, ascensions: 1, identities_touched: 1, best: 0.30 },
  "/baseline": { owner: "autoascend", per_identity: { "val-dwa-law-fem": { progression: 0.089, deepest: "Dlvl:3", episodes: 15 } }, overall: 0.089 },
  "/objectives": [{ name: "random", kind: "random", aggregation: "asc_median_mean", episodes: 32 }, { name: "val-dwa-law-fem", kind: "identity", aggregation: "mean", episodes: 15 }],
  "/search": [sol(A1, "alice"), sol(A2, "alice"), sol(B1, "bob")],
  "/elites": [elite("val-dwa-law-fem", B1, 0.30, 1, "bob")],
  "/board/val-dwa-law-fem/self-reported": [
    { rank: 1, solution_digest: B1, owner: "bob", episodes: 15, ascensions: 1, median_progression: 0.30, mean_progression: 0.30 },
    { rank: 2, solution_digest: A1, owner: "alice", episodes: 15, ascensions: 0, median_progression: 0.20, mean_progression: 0.20 },
    { rank: 3, solution_digest: A2, owner: "alice", episodes: 15, ascensions: 0, median_progression: 0.05, mean_progression: 0.05 },
  ],
  "/board/val-dwa-law-fem/verified": [],
  "/board/random/self-reported": [],
  "/board/coverage": [{ rank: 1, solution_digest: B1, owner: "bob", cells_held: 40 }],
  "/board/firsts": [{ rank: 1, solution_digest: B1, owner: "bob", firsts: 35 }],
  "/progress/self-reported": { series: [{ t: "2026-08-24", frontier: 0.30, ascensions: 1, coverage: 40 }] },
  "/progress/verified": { series: [] },
};

function routeWith(canned) {
  return (p, q) => {
    if (p === "/stats" || p === "/baseline" || p === "/objectives") return canned[p];
    if (p === "/search") { // honor pagination (limit/offset) so the loop is exercised
      const limit = +q.get("limit") || 50, offset = +q.get("offset") || 0;
      return canned["/search"].slice(offset, offset + limit);
    }
    if (p === "/elites") return canned["/elites"];
    if (p === "/board") {
      if (q.get("metric") === "coverage") return canned["/board/coverage"];
      if (q.get("metric") === "firsts") return canned["/board/firsts"];
      const key = `/board/${q.get("objective")}/${q.get("tier") || "self-reported"}`;
      if (key in canned) return canned[key];
      throw new Error("404 board " + key);
    }
    if (p === "/progress") return canned[`/progress/${q.get("tier") || "self-reported"}`];
    if (p.startsWith("/solutions/") && p.endsWith("/frontier")) {
      // the page encodeURIComponent()s the digest (colon -> %3A); the real server
      // decodes it (verified), so decode here too before matching.
      const seg = decodeURIComponent(p.split("/")[2]);
      return canned["/frontier/" + seg.replace(/^sha256:/, "").slice(0, 7)] ?? [];
    }
    throw new Error("no canned route for " + p);
  };
}
const routeLive = routeWith(CANNED_LIVE);
const routeMulti = routeWith(CANNED_MULTI);

function makeFetch(router) {
  return (input) => {
    const url = typeof input === "string" ? input : input.url;
    if (router === "reject") return Promise.reject(new Error("network down: " + url));
    const u = new URL(url, "https://x.test");
    let data;
    try { data = router(u.pathname, u.searchParams); }
    catch (e) { return Promise.resolve({ ok: false, status: 404, json: async () => ({}) }); }
    return Promise.resolve({ ok: true, status: 200, json: async () => data });
  };
}

function build(router) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errors.push("jsdomError: " + (e.detail || e.message || e)));
  vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, url: "https://x.test/", virtualConsole: vc,
    beforeParse(w) {
      w.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
      w.requestAnimationFrame = cb => setTimeout(cb, 0);
      let st = {};
      Object.defineProperty(w, "localStorage", { value: { getItem: k => (k in st ? st[k] : null), setItem: (k, v) => { st[k] = String(v); }, removeItem: k => { delete st[k]; } } });
      w.fetch = makeFetch(router);
    },
  });
  return { dom, errors };
}

const tick = (ms = 25) => new Promise(r => setTimeout(r, ms));
async function waitFor(fn, tries = 60) { for (let i = 0; i < tries; i++) { if (fn()) return true; await tick(15); } return false; }
const click = (dom, el) => el.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
const change = (dom, el) => el.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
const btnView = (doc, v) => [...doc.querySelectorAll("[data-view]")].find(b => b.dataset.view === v);
const regimeInput = (doc, v) => [...doc.querySelectorAll('input[name="regime"]')].find(r => r.value === v);
const odoText = (doc, sel) => [...doc.querySelectorAll(sel + " span")].map(s => s.textContent).join("");

const R = [];
const chk = (name, cond, extra = "") => R.push([cond ? "PASS" : "FAIL", name, extra]);

async function run() {
  // ---------- pass 1: happy path (canned live snapshot) ----------
  {
    const { dom, errors } = build(routeLive);
    const doc = dom.window.document, $ = s => doc.querySelector(s);
    await waitFor(() => odoText(doc, "#odoProg") === "00003");
    await tick(40);

    chk("no script errors (happy path)", errors.length === 0, errors.join(" | "));
    chk("counter: 3 programs", odoText(doc, "#odoProg") === "00003", "got " + odoText(doc, "#odoProg"));
    chk("counter: 1 hacker", odoText(doc, "#odoPlay") === "00001", "got " + odoText(doc, "#odoPlay"));

    const progRows = doc.querySelectorAll("#scBody tr[data-d]:not(.base)");
    chk("board: 3 program rows", progRows.length === 3, "got " + progRows.length);
    chk("board: 1 AutoAscend baseline row", doc.querySelectorAll("#scBody tr.base").length === 1 && $("#scBody tr.base").getAttribute("data-d") === "autoascend");
    chk("board: 3 programs + baseline = 4 rows", doc.querySelectorAll("#scBody tr").length === 4, "got " + doc.querySelectorAll("#scBody tr").length);
    chk("board: baseline shows real deepest (Dlvl:3)", $("#scBody tr.base").textContent.includes("Dlvl:3"));
    chk("board: rank-1 mean progression 0.167 present", [...progRows].some(tr => tr.textContent.includes("0.167")));
    chk("board: deepest column blank '—' for programs (honest GAP)", [...progRows].every(tr => tr.querySelector("td.pend").textContent.trim() === "—"));
    chk("board: 0 stars (no ascensions)", [...doc.querySelectorAll("#scBody .star")].every(s => s.textContent.trim() === "."));

    const who = doc.querySelectorAll(".callouts .callout .who");
    chk("callout: coverage = @vkurenkov 32 cells", who[0].textContent.includes("vkurenkov") && who[0].textContent.includes("32"));
    chk("callout: firsts = @vkurenkov 28 firsts", who[1].textContent.includes("vkurenkov") && who[1].textContent.includes("28"));

    const lit = doc.querySelectorAll('#rolegrid td.vv[style*="background"]');
    chk("frontier(universe): 13 role tables", doc.querySelectorAll("#rolegrid table.fr").length === 13);
    chk("frontier(universe): exactly 1 lit cell", lit.length === 1, "got " + lit.length);
    chk("frontier(universe): lit cell is val-dwa-law-fem",
      [...doc.querySelectorAll("#rolegrid tr[title]")].some(tr => tr.getAttribute("title").includes("val-dwa-law-fem") && tr.querySelector('td.vv[style*="background"]')));
    chk("frontier(universe): ZERO ascension cells (honest)", doc.querySelectorAll("#rolegrid td.asc").length === 0);

    const chart = $("#chart").innerHTML;
    chk("chart: has AutoAscend baseline bar", chart.includes('class="base"'));
    chk("chart: day-bucketed point label (2026-08-23)", chart.includes("2026-08-23"));
    chk("chart: baseline value 0.089", chart.includes("0.089"));
    chk("chart: 3 data rows + axis = 4 lines", chart.split("\n").length === 4, "lines " + chart.split("\n").length);

    // popup: lazy-load a program's frontier
    click(dom, doc.querySelector("#scBody tr[data-d]:not(.base)"));
    await waitFor(() => $("#modal").hasAttribute("open"));
    chk("popup: opens and lazy-loads per-identity frontier (val-dwa-law-fem)", $("#modal").hasAttribute("open") && $("#mBody").textContent.includes("val-dwa-law-fem"));
    $("#modal").removeAttribute("open");

    // ---- Hackers (people) view: per-owner aggregate from /search + /board ----
    click(dom, btnView(doc, "people"));
    await tick(15);
    {
      const hackerRows = [...doc.querySelectorAll("#scBody tr:not(.base)")];
      chk("people: 1 hacker row", hackerRows.length === 1, "got " + hackerRows.length);
      const td = hackerRows[0] && hackerRows[0].querySelectorAll("td");
      chk("people: row is @vkurenkov", !!td && td[1].textContent.includes("vkurenkov"));
      chk("people: deepest '—' (honest GAP)", !!td && td[2].textContent.trim() === "—");
      chk("people: best 0.167 (max over their board rows)", !!td && td[3].textContent.trim() === "0.167");
      chk("people: progs 3 (grouped from /search)", !!td && td[4].textContent.trim() === "3");
      chk("people: 0 ascensions '.'", !!td && td[5].textContent.trim() === ".");
      chk("people: AutoAscend baseline row present", doc.querySelectorAll("#scBody tr.base").length === 1);
    }
    click(dom, btnView(doc, "programs")); // restore
    await tick(15);

    // ---- Program frontier regime: the champion's per-identity cells ----
    change(dom, regimeInput(doc, "program"));
    await tick(15);
    {
      const litP = doc.querySelectorAll('#rolegrid td.vv[style*="background"]');
      chk("program regime: champion lit 2 cells (val + wiz)", litP.length === 2, "got " + litP.length);
      chk("program regime: note names champion nethacker@e11151f (@vkurenkov)", $("#gridnote").textContent.includes("e11151f") && $("#gridnote").textContent.includes("vkurenkov"));
      chk("program regime: no fabricated ascension cells", doc.querySelectorAll("#rolegrid td.asc").length === 0);
    }
    change(dom, regimeInput(doc, "universe")); // restore
    await tick(15);

    // ---------- verified tier: baseline only ----------
    click(dom, [...doc.querySelectorAll("[data-tier]")].find(b => b.dataset.tier === "verified"));
    await waitFor(() => doc.querySelectorAll("#scBody tr[data-d]:not(.base)").length === 0 && doc.querySelector("#scBody").textContent.toLowerCase().includes("verif"));
    await tick(30);
    chk("verified tier: no program data rows", doc.querySelectorAll("#scBody tr[data-d]:not(.base)").length === 0);
    chk("verified tier: baseline row still present", doc.querySelectorAll("#scBody tr.base").length === 1);
    chk("verified tier: friendly 'no verified' notice", doc.querySelector("#scBody").textContent.toLowerCase().includes("verif"));
    chk("verified tier: frontier fully blank", doc.querySelectorAll('#rolegrid td.vv[style*="background"]').length === 0);
    chk("verified tier: chart shows baseline only", $("#chart").innerHTML.split("\n").length === 2);
    dom.window.close();
  }

  // ---------- pass 2: multi-owner Hackers aggregation + ranking ----------
  {
    const { dom, errors } = build(routeMulti);
    const doc = dom.window.document;
    await waitFor(() => odoText(doc, "#odoProg") === "00003");
    await tick(40);
    chk("multi: no script errors", errors.length === 0, errors.join(" | "));
    click(dom, btnView(doc, "people"));
    await tick(15);
    const rows = [...doc.querySelectorAll("#scBody tr:not(.base)")];
    chk("multi people: 2 hacker rows", rows.length === 2, "got " + rows.length);
    const r1 = rows[0] && rows[0].querySelectorAll("td"), r2 = rows[1] && rows[1].querySelectorAll("td");
    chk("multi people: rank1 @bob progs=1 best=0.300 asc=1",
      !!r1 && r1[1].textContent.includes("bob") && r1[3].textContent.trim() === "0.300" && r1[4].textContent.trim() === "1" && r1[5].textContent.trim() === "1");
    chk("multi people: rank2 @alice progs=2 best=0.200 asc='.'",
      !!r2 && r2[1].textContent.includes("alice") && r2[3].textContent.trim() === "0.200" && r2[4].textContent.trim() === "2" && r2[5].textContent.trim() === ".");
    chk("multi people: ranked best-desc (bob before alice)", !!r1 && !!r2 && r1[1].textContent.includes("bob") && r2[1].textContent.includes("alice"));
    chk("multi people: AutoAscend baseline row present", doc.querySelectorAll("#scBody tr.base").length === 1);
    dom.window.close();
  }

  // ---------- pass 3: every fetch rejects -> friendly empty state, clean console ----------
  {
    const { dom, errors } = build("reject");
    const doc = dom.window.document;
    await waitFor(() => odoText(doc, "#odoProg") === "00000" && doc.querySelector("#scBody").textContent.trim().length > 0);
    await tick(60);
    chk("offline: NO uncaught console.error / jsdomError", errors.length === 0, errors.join(" | "));
    chk("offline: counters fall back to 0", odoText(doc, "#odoProg") === "00000" && odoText(doc, "#odoPlay") === "00000");
    chk("offline: board shows friendly 'be the first' empty state", doc.querySelector("#scBody").textContent.toLowerCase().includes("be the first"));
    chk("offline: baseline row still renders (0.000)", doc.querySelectorAll("#scBody tr.base").length === 1);
    chk("offline: callouts fall back to '—' (no stale numbers)", [...doc.querySelectorAll(".callouts .callout .who")].every(w => w.textContent.includes("—")));
    chk("offline: frontier fully blank", doc.querySelectorAll('#rolegrid td.vv[style*="background"]').length === 0);
    dom.window.close();
  }

  const fails = R.filter(r => r[0] === "FAIL");
  for (const [s, n, x] of R) console.log(`${s}  ${n}${x ? "  (" + x + ")" : ""}`);
  console.log(`\n${R.length - fails.length}/${R.length} passed`);
  process.exit(fails.length ? 1 : 0);
}
run();
