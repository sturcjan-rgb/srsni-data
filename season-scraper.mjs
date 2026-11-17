#!/usr/bin/env node
// Season backbone scraper for Sršni Photomate Písek.
// Source: server-rendered NBL team page (both NBL + FIBA LiveStats match IDs live here).
// Writes/merges data/season-YYYY-YY.json. Merge-not-overwrite, keyed by nblMatchId,
// and written to the season file matching the scraped season heading (rollover-safe).
//
// Run:  node season-scraper.mjs           (live fetch + merge)
//       node season-scraper.mjs --debug   (dump raw row text, no write)
//       node season-scraper.mjs --html f  (parse a saved HTML file instead of fetching)

import { load } from "cheerio";
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const TEAM_URL = "https://nbl.basketball/tym/srsni-photomate-pisek";
const TEAM = "Sršni Photomate Písek";
const TEAM_ID = 10110;
const OUT_DIR = resolve(process.cwd(), "data");
// Ručně zadaná FIBA ID ({"<nblMatchId>": <fibaMatchId>}) — pojistka, když nbl.basketball
// FIBA odkaz neukáže nebo je nedostupný (od 11/2026 vrací runnerům GitHub Actions HTTP 403).
const OVERRIDES_FILE = resolve(OUT_DIR, "fiba-overrides.json");
const FIBA_DATA = (id) => `https://fibalivestats.dcd.shared.geniussports.com/data/${id}/data.json`;
const ALIASES = ["srsni", "sršni", "sokol", "pisek", "písek", "photomate"];

// Canonical opponent names — used to split the "domácí / hosté" cell unambiguously.
const KNOWN_TEAMS = [
  "Sršni Photomate Písek", "USK Praha", "SLUNETA Ústí nad Labem",
  "SK Slavia Praha ERA NBK", "PUMPA Basket Brno", "NH Ostrava", "BK Opava",
  "BK Olomoucko", "BK Loko BaliMania Plzeň", "BK Lokomotiva Plzeň", "BK KVIS Pardubice",
  "BK GAPA Hradec Králové", "BK ARMEX ENERGY Děčín", "ERA Basketball Nymburk",
];

const norm = (s) => (s || "").toLowerCase();
const isUs = (name) => ALIASES.some((a) => norm(name).includes(a));

// --- Europe/Prague offset for a wall-clock time (DST-safe; games never at 02:00–03:00) ---
function pragueOffsetMin(y, mo, d, h, mi) {
  const asUTC = Date.UTC(y, mo - 1, d, h, mi);
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Prague", hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
  const p = Object.fromEntries(dtf.formatToParts(new Date(asUTC)).map((x) => [x.type, x.value]));
  const pragueAsUTC = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  return (pragueAsUTC - asUTC) / 60000;
}
function toIso(y, mo, d, h, mi) {
  const off = pragueOffsetMin(y, mo, d, h, mi);
  const sign = off >= 0 ? "+" : "-";
  const ah = Math.floor(Math.abs(off) / 60), am = Math.abs(off) % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return `${y}-${pad(mo)}-${pad(d)}T${pad(h)}:${pad(mi)}:00${sign}${pad(ah)}:${pad(am)}`;
}

// --- shared derivations (mirror of build_season.py) ---
function splitQuarters(nums, finalH, finalA) {
  const pairs = [];
  for (let i = 0; i + 1 < nums.length; i += 2) pairs.push([nums[i], nums[i + 1]]);
  const checkpoints = [...pairs, [finalH, finalA]];
  const byPeriod = [];
  let ph = 0, pa = 0;
  for (const [ch, ca] of checkpoints) { byPeriod.push({ home: ch - ph, away: ca - pa }); ph = ch; pa = ca; }
  return {
    byPeriod, regulation: byPeriod.slice(0, 4), overtimes: byPeriod.slice(4),
    otCount: Math.max(0, byPeriod.length - 4),
    cumulative: checkpoints.map(([h, a]) => ({ home: h, away: a })),
  };
}

export function splitTeams(cellText) {
  // Find known team names by their position in the cell; the two present are home/away in order.
  const found = [];
  for (const t of KNOWN_TEAMS) {
    const idx = cellText.indexOf(t);
    if (idx !== -1) found.push({ t, idx });
  }
  found.sort((a, b) => a.idx - b.idx);
  if (found.length >= 2) return [found[0].t, found[1].t];
  // Soupeř, kterého seznam nezná (nový tým, přejmenování sponzorem): zbytek buňky po odečtení nás.
  const ours = found.find((f) => isUs(f.t));
  if (ours) {
    const before = cellText.slice(0, ours.idx).replace(/[\s–—:-]+$/, "").trim();
    const after = cellText.slice(ours.idx + ours.t.length).replace(/^[\s–—:-]+/, "").trim();
    if (after && !before) { console.warn(`WARN neznámý tým "${after}" — doplň do KNOWN_TEAMS`); return [ours.t, after]; }
    if (before && !after) { console.warn(`WARN neznámý tým "${before}" — doplň do KNOWN_TEAMS`); return [before, ours.t]; }
  }
  return [null, null];
}

function buildFixture({ round, dateStr, timeStr, home, away, hs, as_, quarterNums, nblId, fibaId, phase }) {
  const [d, mo, y] = dateStr.match(/\d+/g).map(Number);
  const [h, mi] = timeStr.split(":").map(Number);
  const played = hs != null && as_ != null;
  const venue = isUs(home) ? "home" : "away";
  const opponent = venue === "home" ? away : home;
  const us = played ? (venue === "home" ? hs : as_) : null;
  const them = played ? (venue === "home" ? as_ : hs) : null;
  const rm = round && round.match(/(\d+)/);
  return {
    nblMatchId: nblId,
    fibaMatchId: fibaId ?? null,
    round: round || null,
    roundNum: rm ? +rm[1] : null,
    phase: phase || null,
    date: toIso(y, mo, d, h, mi).slice(0, 10),
    time: timeStr,
    tz: "Europe/Prague",
    datetime: toIso(y, mo, d, h, mi),
    home, away, opponent, venue,
    status: played ? "final" : "scheduled",
    score: {
      home: hs, away: as_, us, them,
      result: played ? (us > them ? "W" : "L") : null,
      margin: played ? us - them : null,
    },
    quarters: played ? splitQuarters(quarterNums, hs, as_) : null,
    links: {
      nbl: `https://nbl.basketball/zapas/${nblId}`,
      livestats: fibaId ? `https://www.fibalivestats.com/webcast/CBFFE/${fibaId}/` : null,
      fibaData: fibaId ? `https://fibalivestats.dcd.shared.geniussports.com/data/${fibaId}/data.json` : null,
    },
  };
}

function parseTeamPage(html, debug = false) {
  const $ = load(html);

  // Season from the "Zápasy YYYY/YY" heading.
  let season = null;
  $("h1,h2,h3,h4").each((_, el) => {
    const m = $(el).text().match(/Zápasy\s+(\d{4})\/(\d{2})/);
    if (m && !season) season = `${m[1]}/${m[2]}`;
  });

  // Locate the fixtures table: the one whose header row mentions "domácí" and "čtvrtiny".
  let table = null, colIdx = {};
  $("table").each((_, tbl) => {
    if (table) return;
    const head = $(tbl).find("tr").first().find("th,td").map((i, c) => norm($(c).text())).get();
    if (head.some((t) => t.includes("domácí")) && head.some((t) => t.includes("čtvrtin"))) {
      table = tbl;
      head.forEach((t, i) => {
        if (t.includes("kolo")) colIdx.round = i;
        else if (t.includes("datum")) colIdx.datetime = i;
        else if (t.includes("domácí")) colIdx.teams = i;
        else if (t.includes("skore") || t.includes("skóre")) colIdx.score = i;
        else if (t.includes("čtvrtin")) colIdx.quarters = i;
        else if (t.includes("fáze")) colIdx.phase = i;
      });
    }
  });
  if (!table) return { season, fixtures: [] };

  const fixtures = [];
  $(table).find("tr").slice(1).each((_, tr) => {
    const $tr = $(tr);
    const rowHtml = $tr.html() || "";
    const cells = $tr.find("td").map((i, c) => $(c)).get();
    if (!cells.length) return;

    const nblId = (rowHtml.match(/\/zapas\/(\d+)/) || [])[1];
    if (!nblId) return; // not a fixture row
    const fibaId = (rowHtml.match(/fibalivestats[^"'\s]*?\/(?:webcast|u)\/CBFFE\/(\d+)/) || [])[1];

    const cellText = (i) => (i != null && cells[i] ? cells[i].text().replace(/\s+/g, " ").trim() : "");
    const round = cellText(colIdx.round);
    const dtText = cellText(colIdx.datetime);
    const teamsText = cellText(colIdx.teams);
    const scoreText = cellText(colIdx.score);
    const quartersText = cellText(colIdx.quarters);
    const phase = cellText(colIdx.phase);

    const dm = dtText.match(/(\d+\.\s*\d+\.\s*\d{4})\s+(\d{1,2}:\d{2})/);
    const [home, away] = splitTeams(teamsText);
    const scoreNums = (scoreText.match(/\d+/g) || []).map(Number);
    const quarterNums = (quartersText.match(/\d+/g) || []).map(Number);

    if (debug) { console.log({ nblId, fibaId, round, dtText, teamsText, scoreText, quartersText, phase }); return; }
    if (!dm || !home || !away) { console.warn(`WARN unparsed row nblId=${nblId}`); return; }

    fixtures.push(buildFixture({
      round, dateStr: dm[1], timeStr: dm[2], home, away,
      hs: scoreNums.length >= 2 ? scoreNums[0] : null,
      as_: scoreNums.length >= 2 ? scoreNums[1] : null,
      quarterNums, nblId: +nblId, fibaId: fibaId ? +fibaId : null, phase,
    }));
  });

  return { season, fixtures };
}

function seasonFile(season) {
  return resolve(OUT_DIR, `season-${season.replace("/", "-20")}.json`); // 2026/27 -> season-2026-2027? see normalize
}
function seasonKey(season) { // "2026/27" -> "2026-27"
  const [a, b] = season.split("/");
  return `${a}-${b}`;
}

// Ligový rozpis z nbl2627 (GitHub Pages) — FIBA ID dohledává i bez nbl.basketball.
const NBL2627_SCHEDULE = (key) => `https://sturcjan-rgb.github.io/nbl2627/data/${key}/schedule.json`;

async function loadNbl2627Ids(season) {
  try {
    const res = await fetch(NBL2627_SCHEDULE(seasonKey(season)));
    if (!res.ok) { console.warn(`WARN rozpis nbl2627: HTTP ${res.status}`); return {}; }
    const out = {};
    for (const f of (await res.json()).fixtures || []) if (f.nblId && f.fibaId) out[String(f.nblId)] = f.fibaId;
    return out;
  } catch (e) { console.warn(`WARN rozpis nbl2627: ${e.message}`); return {}; }
}

function loadOverrides() {
  if (!existsSync(OVERRIDES_FILE)) return {};
  return JSON.parse(readFileSync(OVERRIDES_FILE, "utf-8"));
}

// Doplní fibaMatchId z ručních přepisů (přepis má přednost).
export function applyOverrides(fixtures, overrides) {
  let n = 0;
  for (const f of fixtures) {
    const id = overrides[String(f.nblMatchId)];
    if (!id || f.fibaMatchId === +id) continue;
    f.fibaMatchId = +id;
    f.links = { ...(f.links || {}), livestats: `https://www.fibalivestats.com/webcast/CBFFE/${id}/`, fibaData: FIBA_DATA(id) };
    n++;
  }
  return n;
}

// Výsledek z FIBA data.json (když ho NBL web neukáže): skóre, čtvrtiny, stav.
export function resultFromFiba(f, raw) {
  const t1 = raw?.tm?.["1"], t2 = raw?.tm?.["2"];
  if (!t1 || !t2) return null;
  const ended = (raw.pbp || []).some((e) => e.actionType === "game" && e.subType === "end");
  if (!ended) return null;
  // tm 1 bývá domácí; kontrola podle jména (my vs. venue)
  const swap = isUs(t1.name) !== (f.venue === "home");
  const [H, A] = swap ? [t2, t1] : [t1, t2];
  const hs = +H.score, as_ = +A.score;
  if (!Number.isFinite(hs) || !Number.isFinite(as_)) return null;
  const per = [1, 2, 3, 4].map((p) => [+H[`p${p}_score`] || 0, +A[`p${p}_score`] || 0]);
  const cum = [];
  let ch = 0, ca = 0;
  for (const [h, a] of per) { ch += h; ca += a; cum.push(ch, ca); }
  const nums = (ch === hs && ca === as_) ? cum.slice(0, 6) : cum; // bez prodloužení poslední čtvrtina = konečné skóre
  const us = f.venue === "home" ? hs : as_, them = f.venue === "home" ? as_ : hs;
  return {
    status: "final",
    score: { home: hs, away: as_, us, them, result: us > them ? "W" : "L", margin: us - them },
    quarters: splitQuarters(nums, hs, as_),
  };
}

async function fillResultsFromFiba(fixtures) {
  const now = Date.now();
  let n = 0;
  for (const f of fixtures) {
    if (!f.fibaMatchId || f.status === "final") continue;
    if (new Date(f.datetime).getTime() + 2 * 3600000 > now) continue;
    try {
      const res = await fetch(FIBA_DATA(f.fibaMatchId), { headers: { "User-Agent": "srsni-agent/1.0" } });
      if (!res.ok) { console.warn(`WARN FIBA ${f.fibaMatchId}: HTTP ${res.status}`); continue; }
      const r = resultFromFiba(f, await res.json());
      if (r) { Object.assign(f, r); n++; }
    } catch (e) { console.warn(`WARN FIBA ${f.fibaMatchId}: ${e.message}`); }
  }
  return n;
}

function latestSeasonFromFiles() {
  if (!existsSync(OUT_DIR)) return null;
  const keys = readdirSync(OUT_DIR).map((n) => (n.match(/^season-(\d{4})-(\d{2})\.json$/) || []).slice(1)).filter((m) => m.length);
  if (!keys.length) return null;
  keys.sort();
  const [a, b] = keys[keys.length - 1];
  return `${a}/${b}`;
}

function readSeasonFixtures(season) {
  const file = resolve(OUT_DIR, `season-${seasonKey(season)}.json`);
  return existsSync(file) ? JSON.parse(readFileSync(file, "utf-8")).fixtures || [] : [];
}

// Merge-not-overwrite podle nblMatchId; prázdné hodnoty z NBL nemažou už známé.
export function mergeFixtures(existing, fixtures) {
  const byId = new Map(existing.map((f) => [f.nblMatchId, f]));
  for (const f of fixtures) {
    const old = byId.get(f.nblMatchId) || {};
    const next = { ...old, ...f };
    // NBL web někdy FIBA odkaz / výsledek neukáže — už známé hodnoty nemazat
    if (next.fibaMatchId == null && old.fibaMatchId != null) { next.fibaMatchId = old.fibaMatchId; next.links = old.links; }
    if (next.status !== "final" && old.status === "final") Object.assign(next, { status: old.status, score: old.score, quarters: old.quarters });
    byId.set(f.nblMatchId, next);
  }
  return [...byId.values()].sort((a, b) => a.datetime.localeCompare(b.datetime));
}

function mergeWrite(season, fixtures) {
  if (!existsSync(OUT_DIR)) mkdirSync(OUT_DIR, { recursive: true });
  const file = resolve(OUT_DIR, `season-${seasonKey(season)}.json`);
  let existing = { fixtures: [] };
  if (existsSync(file)) existing = JSON.parse(readFileSync(file, "utf-8"));
  const merged = mergeFixtures(existing.fixtures || [], fixtures);
  const out = {
    season,
    team: { name: TEAM, nblId: TEAM_ID, nblSlug: "srsni-photomate-pisek" },
    generatedAt: new Date().toISOString(),
    source: TEAM_URL,
    counts: {
      total: merged.length,
      home: merged.filter((f) => f.venue === "home").length,
      away: merged.filter((f) => f.venue === "away").length,
      final: merged.filter((f) => f.status === "final").length,
      scheduled: merged.filter((f) => f.status === "scheduled").length,
    },
    fixtures: merged,
  };
  writeFileSync(file, JSON.stringify(out, null, 2) + "\n", "utf-8");
  return { file, counts: out.counts };
}

async function main() {
  const args = process.argv.slice(2);
  const debug = args.includes("--debug");
  const htmlArg = args.indexOf("--html");
  let html = null;
  if (htmlArg !== -1) html = readFileSync(args[htmlArg + 1], "utf-8");
  else {
    try {
      const res = await fetch(TEAM_URL, { headers: { "User-Agent": "srsni-agent/1.0" } });
      if (res.ok) html = await res.text();
      else console.warn(`WARN ${TEAM_URL} -> HTTP ${res.status}`);
    } catch (e) { console.warn(`WARN ${TEAM_URL} -> ${e.message}`); }
  }
  let season, fixtures;
  if (html) {
    ({ season, fixtures } = parseTeamPage(html, debug));
    if (debug) return;
    if (!season) { console.error("No season heading found — aborting (won't guess season file)."); process.exit(2); }
    fixtures = mergeFixtures(readSeasonFixtures(season), fixtures);
  } else {
    // NBL web nedostupný: pokračovat s uloženým rozpisem (ruční FIBA ID + výsledky z FIBA)
    season = latestSeasonFromFiles();
    if (!season) { console.error("NBL nedostupné a žádný uložený rozpis — končím."); process.exit(1); }
    fixtures = readSeasonFixtures(season);
    console.warn(`NBL nedostupné — používám uložený rozpis ${season} (${fixtures.length} zápasů)`);
  }
  // jen chybějící ID z nbl2627, ruční přepisy pak mají přednost
  const fromLeague = await loadNbl2627Ids(season);
  for (const f of fixtures) if (f.fibaMatchId != null) delete fromLeague[String(f.nblMatchId)];
  const nLeague = applyOverrides(fixtures, fromLeague);
  if (nLeague) console.log(`FIBA ID z nbl2627: ${nLeague}`);
  const nOv = applyOverrides(fixtures, loadOverrides());
  const nFiba = await fillResultsFromFiba(fixtures);
  console.log(`ruční FIBA ID: ${nOv}, výsledky z FIBA: ${nFiba}`);
  const { file, counts } = mergeWrite(season, fixtures);
  console.log(`[${season}] ${fixtures.length} fixtures → ${file}`);
  console.log(counts);
}

if (import.meta.url === `file://${process.argv[1]}`) main().catch((e) => { console.error(e); process.exit(1); });
