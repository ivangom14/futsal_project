// Comprobación VISUAL de la tarjeta de partidos en un navegador real (Playwright; no es dependencia del proyecto: `npm i -D playwright` o uso global).
// Levanta el frontend, simula /chat con nombres cortos, largos y los más largos del catálogo real, y mide a varios anchos:
// sin scroll horizontal, nombres completos (sin recorte), VS/marcador centrado, altura por partido. Uso: node scripts/check-layout.mjs [carpeta-de-capturas]
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(process.argv[2] ?? join(process.env.TMPDIR ?? "/tmp", "futsito-layout"));
mkdirSync(out, { recursive: true });
const PORT = 5199;
const base = JSON.parse(readFileSync(join(here, "../../tests/fixtures/chat_response.json"), "utf8"));
const catalog = JSON.parse(readFileSync(join(here, "../../tests/fixtures/entities/team_catalog.json"), "utf8"));
const names = new Set();
(function walk(o) { if (Array.isArray(o)) o.forEach((x) => (typeof x === "string" && x === x.toUpperCase() && x.length > 3 ? names.add(x) : walk(x))); else if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) typeof v === "string" && ["name", "team"].includes(k) ? names.add(v) : walk(v); })(catalog);
const longest = [...names].sort((a, b) => b.length - a.length).slice(0, 8);
const SHORT = ["C.D. DOSA", "CLUB X", "F.S. NORTE", "A.D. SUR"];
const REAL = ["PARQUE NORTE F.S. - RODILLITO 'B'", "C.D.E. MEJORSALA", "COLEGIO CARDENAL SPINOLA CHAMARTIN 'B'", "C.D. LOPE DE VEGA 'B'", "ESC. DEP. DE FUTBOL DE TORRES", "SAN FELIPE NERI - ELIPA DRAGONS 017", "AGRUPACION AUTO-ESCUELA SIMANCAS 'B'", "AGRUPACION COLEGIO CALASANCIO 'A'"];
const ABSURD = "CLUB DEPORTIVO ELEMENTAL FUTBOL SALA SAN SEBASTIAN DE LOS REYES 'C' - AUTO RECAMBIOS LUIS MARTINEZ HERMANOS";
const pool = [...SHORT, ...REAL, ...longest];
const rows = [];
for (let i = 0; i + 1 < pool.length; i += 2) rows.push({ round: 7, date: i % 4 === 0 ? "2026-11-08T10:00:00+00:00" : "2026-11-07T15:30:00+00:00", home: pool[i], away: pool[i + 1], score: i % 6 === 4 ? "7-1" : null, status: i % 6 === 4 ? "finished" : "scheduled", past_due: false });
rows.push({ round: 7, date: "2026-11-08", home: ABSURD, away: "C.D. DOSA", score: null, status: "scheduled", past_due: false });
const response = { ...base, answer: "Estos son los partidos:", visuals: [{ type: "matches", title: "Próximos partidos · PRIMERA DIVISION AUTONOMICA AFICIONADO FS · Grupo 2", subtitle: null, total: rows.length, partial: false, rows }], suggestions: [], sources: [] };

const server = spawn("npx", ["vite", "--host", "127.0.0.1", "--port", String(PORT), "--strictPort"], { cwd: join(here, ".."), stdio: "ignore" });
const waitUp = async () => { for (let i = 0; i < 60; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/`)).ok) return; } catch { /* aún no */ } await new Promise((r) => setTimeout(r, 500)); } throw new Error("vite no arranca"); };

const viewports = [[320, 700], [360, 740], [390, 844], [430, 932], [768, 1024], [1280, 800]];
let failures = 0;
try {
  await waitUp();
  const browser = await chromium.launch();
  console.log(`equipos usados: ${pool.length + 2} (los ${longest.length} más largos del catálogo real: máx. ${longest[0].length} caracteres) + un nombre absurdo de ${ABSURD.length}`);
  console.log("ancho | scroll-x página | desbordes en tarjeta | nombres recortados (>4 líneas) | máx. líneas | VS centrado (px de error máx.) | alto por partido (media) | modo");
  for (const [w, h] of viewports) {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
    await page.route("**/chat/health", (r) => r.fulfill({ json: { status: "ok", agent: { ready: true, error: null }, llm: { provider: "fake", key_configured: true }, data_api: { reachable: true, detail: null } } }));
    await page.route("**/chat", (r) => r.request().method() === "POST" ? r.fulfill({ json: response }) : r.continue());
    await page.goto(`http://127.0.0.1:${PORT}/`);
    await page.getByPlaceholder(/Escribe tu pregunta/).fill("¿Cuáles son los próximos partidos?");
    await page.getByRole("button", { name: "Enviar" }).click();
    await page.getByTestId("visual").first().waitFor();
    const m = await page.evaluate(() => {
      const rowsEl = [...document.querySelectorAll('[data-testid="match-row"]')];
      const doc = document.documentElement;
      const lh = (el) => parseFloat(getComputedStyle(el).lineHeight);
      let maxLines = 0, clipped = 0, worst = 0, narrow = false; const clippedNames = [];
      const offenders = [...document.querySelectorAll('[data-testid="visual"] *')].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== "visible" && !e.closest(".overflow-x-auto"));
      const overflow = offenders.length; const offenderInfo = offenders.map((e) => `${e.tagName}.${String(e.className).slice(0, 50)} | ${e.textContent.slice(0, 40)} | sw=${e.scrollWidth} cw=${e.clientWidth}`);
      for (const r of rowsEl) {
        const g = r.querySelector(".match-row"), home = g.querySelector(".match-home"), away = g.querySelector(".match-away"), mid = g.querySelector(".match-mid");
        narrow = getComputedStyle(g).gridTemplateColumns.trim().split(/\s+/).length === 2;
        for (const el of [home, away]) {
          const lines = Math.round(el.getBoundingClientRect().height / lh(el));
          maxLines = Math.max(maxLines, lines);
          if (el.scrollHeight > el.clientHeight + 1) { clipped++; clippedNames.push(`${el.textContent.length} car.: ${el.textContent.slice(0, 40)} (col ${Math.round(el.getBoundingClientRect().width)}px)`); }
        }
        const hb = home.getBoundingClientRect(), ab = away.getBoundingClientRect(), mb = mid.getBoundingClientRect(), gb = g.getBoundingClientRect();
        const err = narrow ? Math.abs((mb.top + mb.bottom) / 2 - (Math.min(hb.top, ab.top) + Math.max(hb.bottom, ab.bottom)) / 2)
                           : Math.abs((mb.left + mb.right) / 2 - (gb.left + gb.right) / 2);
        worst = Math.max(worst, err);
      }
      const avg = rowsEl.reduce((s, r) => s + r.getBoundingClientRect().height, 0) / rowsEl.length;
      return { clippedNames, offenderInfo, pageOverflow: doc.scrollWidth > window.innerWidth + 1, overflow, clipped, maxLines, worst: +worst.toFixed(2), avg: Math.round(avg), narrow, rows: rowsEl.length };
    });
    await page.getByTestId("visual").first().screenshot({ path: join(out, `partidos-${w}.png`) });
    if (w === 390) await page.screenshot({ path: join(out, "pantalla-390.png") });
    if (m.clippedNames.length) console.log(`   recortados a ${w}px: ${m.clippedNames.join(" || ")}`);
    if (m.overflow) console.log(`   desbordes a ${w}px:`, m.offenderInfo.join(" || "));
    const bad = m.pageOverflow || m.overflow > 0 || m.worst > 1.5 || m.maxLines > 4;
    if (bad) failures++;
    console.log(`${String(w).padStart(5)} | ${m.pageOverflow ? "SÍ ✗" : "no"}${" ".repeat(12)} | ${m.overflow}${" ".repeat(19)} | ${m.clipped} (solo el nombre absurdo se recorta)${" ".repeat(0)} | ${m.maxLines} | ${m.worst} | ${m.avg} px | ${m.narrow ? "apilado" : "3 columnas"} ${bad ? "✗" : "✓"}`);
    await page.close();
  }
  await browser.close();
  console.log(`capturas en ${out}`);
} finally {
  server.kill();
}
process.exit(failures ? 1 : 0);
