import { chromium } from "playwright";
import { mkdirSync } from "fs";

// Parse command-line arguments
const args = process.argv.slice(2);
let outDir = "./viz-shots";

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--out" && i + 1 < args.length) {
    outDir = args[i + 1];
    break;
  }
}

mkdirSync(outDir, { recursive: true });
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
p.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
p.on("pageerror", e => errs.push("PAGEERR " + e.message));

async function shot(url, name, waitMs, scrollY) {
  await p.goto(url, { waitUntil: "domcontentloaded" });
  if (scrollY) { await p.evaluate(y => window.scrollTo(0, y), scrollY); }
  await p.waitForTimeout(waitMs);
  await p.screenshot({ path: `${outDir}/${name}.png` });
}
const U = "http://127.0.0.1:8000/?vizpreview=";
await shot(U + "0", "1-idle", 700, 0);
await shot(U + "0.35", "2-vol35", 1500, 0);
await shot(U + "0.9", "3-vol90", 1700, 0);
await shot(U + "0.9", "4-vol90-scrolled", 1200, 1200);
console.log("preview errors:", errs.length ? errs.slice(0, 6) : "none");

// Dev-only visual check (NOT wired into CI, which is pytest-only): confirm the wall
// actually names registered hackers, not just abstract glyphs.
const handles = await p.evaluate(async () => {
  const r = await fetch("/hackers/random?n=20"); return r.ok ? await r.json() : [];
});
console.log("registered handles on the wall:", handles.length ? handles.join(", ") : "(none — empty registry?)");
await b.close();
