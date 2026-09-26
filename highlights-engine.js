/* Sršni highlights engine — z FIBA LiveStats data.json vytáhne momenty pro sociální sítě.
   Čisté funkce bez DOMu: běží v prohlížeči i v Node, API je na globalThis.SrsniHighlights. */
(function(){
'use strict';

const RUN_MIN = 8;                        // série bez odpovědi od tolika bodů
const COMEBACK_MIN = 6;                   // obrat: smazané manko aspoň tolik bodů
const LEAD_MARKS = [10, 15, 20, 25, 30];  // náskok týmu dosáhl poprvé
const PTS_MARKS = [15, 20, 25, 30, 35, 40];
const THREES_MIN = 4;                     // trojky: 4, 5, 6, …
const CLUTCH_SECS = 120;                  // rozhodující koš v posledních 2 min Q4/prodloužení
const DD_CATS = ['pts', 'reb', 'ast', 'stl', 'blk'];

const gtSec = s => { const [m, x] = String(s || '0:0').split(':').map(Number); return (m || 0) * 60 + (x || 0); };
const mmss = s => Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');

function periodLabel(p) { return p <= 4 ? 'Q' + p : (p === 5 ? 'PP' : 'PP' + (p - 4)); }

function detectOurTeam(d) {
  for (const t of ['1', '2']) {
    const tm = d.tm[t] || {};
    if (/sr[sš]n|p[ií]sek/i.test((tm.name || '') + ' ' + (tm.shortName || ''))) return +t;
  }
  return 1;
}

function playerInfo(d, tno, pno) {
  const p = (d.tm[String(tno)].pl || {})[String(pno)] || {};
  return {
    pno: +pno, tno,
    name: [p.firstName, p.familyName].filter(Boolean).join(' ') || p.name || '',
    short: p.scoreboardName || p.name || '',
    family: p.familyName || p.name || '',
    shirt: p.shirtNumber != null ? String(p.shirtNumber) : ''
  };
}

function isFinished(d) {
  const pbp = d.pbp || [];
  if (pbp.some(e => e.actionType === 'game' && e.subType === 'end')) return true;
  return false;
}

/* Hráč s nejvyšší efektivitou (EFF) v daném týmu z box score. */
function bestPlayer(d, tno) {
  const pls = d.tm[String(tno)].pl || {};
  let best = null;
  for (const k in pls) {
    const p = pls[k];
    const eff = (+p.sPoints || 0) + (+p.sReboundsTotal || 0) + (+p.sAssists || 0) + (+p.sSteals || 0) + (+p.sBlocks || 0)
      - ((+p.sFieldGoalsAttempted || 0) - (+p.sFieldGoalsMade || 0))
      - ((+p.sFreeThrowsAttempted || 0) - (+p.sFreeThrowsMade || 0))
      - (+p.sTurnovers || 0);
    if (!best || eff > best.eff || (eff === best.eff && (+p.sPoints || 0) > best.pts)) {
      best = {
        pno: +k, eff, pts: +p.sPoints || 0, reb: +p.sReboundsTotal || 0, ast: +p.sAssists || 0,
        stl: +p.sSteals || 0, blk: +p.sBlocks || 0, tpm: +p.sThreePointersMade || 0,
        fgm: +p.sFieldGoalsMade || 0, fga: +p.sFieldGoalsAttempted || 0, min: p.sMinutes || ''
      };
    }
  }
  return best && Object.assign(best, playerInfo(d, tno, best.pno));
}

function analyze(d, opts) {
  opts = opts || {};
  const us = opts.ourTeam || detectOurTeam(d);
  const them = us === 1 ? 2 : 1;
  const pl = d.periodLength || d.periodLengthREGULAR || 10;
  const ot = d.periodLengthOVERTIME || 5;
  const elapsedOf = e => e.period <= 4
    ? (e.period - 1) * pl * 60 + pl * 60 - gtSec(e.gt)
    : 4 * pl * 60 + (e.period - 5) * ot * 60 + ot * 60 - gtSec(e.gt);
  const periodSecs = p => (p <= 4 ? pl : ot) * 60;

  const teams = {};
  for (const t of [1, 2]) {
    const tm = d.tm[String(t)];
    teams[t] = {name: tm.name || '', short: tm.shortName || tm.name || '', code: tm.code || ''};
  }
  const meta = {us, them, teams, home: teams[1], away: teams[2]};

  const pbp = [...(d.pbp || [])].sort((a, b) => a.actionNumber - b.actionNumber);
  const out = [];
  const moment = (e, s1, s2) => ({
    action: e.actionNumber, el: elapsedOf(e), period: e.period,
    clock: periodLabel(e.period) + ' · ' + mmss(gtSec(e.gt)), s1, s2
  });

  // průběžný stav
  let s1 = 0, s2 = 0;
  let run = null;                                   // {team, pts, oppStart, start, startScore}
  const maxDeficit = {1: 0, 2: 0};                  // největší manko týmu od chvíle, kdy naposledy nevedl
  const leadMarkHit = {1: {}, 2: {}};
  const stats = {1: {}, 2: {}};
  const hit = {};                                   // už ohlášené milníky hráčů
  const st = (t, p) => stats[t][p] || (stats[t][p] = {pts: 0, reb: 0, ast: 0, stl: 0, blk: 0, tpm: 0});

  function pushRun(r) {
    if (!r || r.pts < RUN_MIN) return;
    const m = moment(r.lastEvent, r.endScore[0], r.endScore[1]);
    out.push(Object.assign(m, {
      id: 'run-' + r.start, type: 'run', team: r.team, ongoing: !!r.ongoing,
      pts: r.pts, fromScore: r.startScore,
      version: 'run-' + r.start + '-' + r.pts + (r.ongoing ? '' : '-end')
    }));
  }

  for (const e of pbp) {
    const t = +e.tno;
    const pno = +e.pno;
    const type = e.actionType;
    const made = +e.success === 1;

    // statistiky hráčů z play-by-play (kvůli času milníků)
    if (t && pno) {
      const p = st(t, pno);
      if (made && (type === '2pt' || type === '3pt' || type === 'freethrow')) {
        p.pts += type === '3pt' ? 3 : type === '2pt' ? 2 : 1;
        if (type === '3pt') p.tpm++;
      } else if (type === 'rebound') p.reb++;
      else if (type === 'assist') p.ast++;
      else if (type === 'steal') p.stl++;
      else if (type === 'block') p.blk++;
    }

    const ns1 = e.s1 !== '' && e.s1 != null ? +e.s1 : s1;
    const ns2 = e.s2 !== '' && e.s2 != null ? +e.s2 : s2;
    const scored = (ns1 !== s1 || ns2 !== s2) && t;

    if (scored) {
      const scorer = ns1 > s1 ? 1 : 2;
      const gained = scorer === 1 ? ns1 - s1 : ns2 - s2;
      const prevLead = s1 - s2;

      // série
      if (run && run.team === scorer) run.pts += gained;
      else {
        if (run) { run.ongoing = false; pushRun(run); }
        run = {team: scorer, pts: gained, start: e.actionNumber, startScore: [s1, s2]};
      }
      s1 = ns1; s2 = ns2;
      run.lastEvent = e;
      run.endScore = [s1, s2];
      const lead = s1 - s2;

      // obrat / převzetí vedení
      for (const tm of [1, 2]) {
        const myLead = tm === 1 ? lead : -lead;
        const myPrev = tm === 1 ? prevLead : -prevLead;
        if (myLead < 0) maxDeficit[tm] = Math.max(maxDeficit[tm], -myLead);
        if (myLead > 0 && myPrev <= 0) {
          const inClutch = e.period >= 4 && gtSec(e.gt) <= CLUTCH_SECS;
          if (inClutch) {
            out.push(Object.assign(moment(e, s1, s2), {
              id: 'clutch-' + e.actionNumber, type: 'clutch', team: tm,
              player: pno ? playerInfo(d, tm, pno) : null, shot: type,
              deficit: maxDeficit[tm] >= COMEBACK_MIN ? maxDeficit[tm] : 0,
              version: 'clutch-' + e.actionNumber
            }));
          } else if (maxDeficit[tm] >= COMEBACK_MIN) {
            out.push(Object.assign(moment(e, s1, s2), {
              id: 'comeback-' + e.actionNumber, type: 'comeback', team: tm, deficit: maxDeficit[tm],
              version: 'comeback-' + e.actionNumber
            }));
          }
          maxDeficit[tm] = 0;
        }
        for (const mk of LEAD_MARKS) {
          if (myLead >= mk && !leadMarkHit[tm][mk]) {
            leadMarkHit[tm][mk] = true;
            // ohlas jen nejvyšší nově dosaženou hranici
            if (!LEAD_MARKS.some(x => x > mk && myLead >= x)) {
              out.push(Object.assign(moment(e, s1, s2), {
                id: 'lead-' + tm + '-' + mk, type: 'lead', team: tm, mark: mk, lead: myLead,
                version: 'lead-' + tm + '-' + mk
              }));
            }
          }
        }
      }
    }

    // milníky hráčů
    if (t && pno && stats[t][pno]) {
      const p = stats[t][pno];
      const key = t + '-' + pno;
      const once = (id, extra) => {
        if (hit[key + id]) return;
        hit[key + id] = true;
        out.push(Object.assign(moment(e, s1, s2), {
          id: 'pl-' + key + '-' + id, version: 'pl-' + key + '-' + id, team: t,
          player: playerInfo(d, t, pno), line: Object.assign({}, p)
        }, extra));
      };
      const ptsMark = PTS_MARKS.filter(m => p.pts >= m).pop();
      if (ptsMark) once('pts' + ptsMark, {type: 'points', mark: ptsMark});
      if (p.tpm >= THREES_MIN) once('3pm' + p.tpm, {type: 'threes', mark: p.tpm});
      const tens = DD_CATS.filter(c => p[c] >= 10);
      if (tens.length >= 3) once('td', {type: 'tripledouble', cats: tens.slice(0, 3)});
      else if (tens.length === 2) once('dd', {type: 'doubledouble', cats: tens});
    }
  }
  if (run) {
    run.ongoing = !isFinished(d);
    pushRun(run);
  }

  const finished = isFinished(d);
  const result = dedupe(out).sort((a, b) => a.action - b.action);
  const last = pbp[pbp.length - 1];
  const now = last ? moment(last, s1, s2) : {el: 0, period: 1, clock: 'Q1 · ' + mmss(pl * 60), s1: 0, s2: 0};

  // hráč zápasu (po konci) / zatím nejlepší
  const mvp = bestPlayer(d, us);
  if (mvp) {
    result.push(Object.assign({}, now, {
      id: 'mvp', type: 'mvp', team: us, final: finished, player: mvp,
      version: 'mvp-' + (finished ? 'final-' : '') + mvp.pno + '-' + mvp.eff
    }));
  }

  for (const h of result) {
    h.ours = h.team === us;
    h.caption = caption(h, meta);
    Object.assign(h, headline(h, meta));
  }
  meta.finished = finished;
  meta.now = now;
  meta.score = {1: s1, 2: s2};
  return {meta, highlights: result};
}

function dedupe(list) {
  const seen = new Map();
  for (const h of list) seen.set(h.id, h);   // pozdější verze (větší série) přepíše dřívější
  return [...seen.values()];
}

/* ===== texty ===== */
const plural = (one, few, many) => n => n === 1 ? one : (n >= 2 && n <= 4 ? few : many);
const ptsWord = plural('bod', 'body', 'bodů');
const threesWord = plural('trojka', 'trojky', 'trojek');
const WORDS = {pts: ptsWord, reb: plural('doskok', 'doskoky', 'doskoků'), ast: plural('asistence', 'asistence', 'asistencí'),
  stl: plural('zisk', 'zisky', 'zisků'), blk: plural('blok', 'bloky', 'bloků')};
const cat = (c, n) => n + ' ' + WORDS[c](n);
function scoreLine(h, meta) {
  return meta.home.short + ' ' + h.s1 + ':' + h.s2 + ' ' + meta.away.short;
}
function statLine(l) {
  const parts = [cat('pts', l.pts)];
  if (l.reb) parts.push(cat('reb', l.reb));
  if (l.ast) parts.push(cat('ast', l.ast));
  if (l.stl >= 3) parts.push(cat('stl', l.stl));
  if (l.blk >= 3) parts.push(cat('blk', l.blk));
  return parts.join(' · ');
}
function resultLine(h, meta) {
  const ours = meta.us === 1 ? h.s1 : h.s2, theirs = meta.us === 1 ? h.s2 : h.s1;
  if (ours > theirs) return '🐝 VÝHRA ' + ours + ':' + theirs + '! ';
  if (ours < theirs) return 'Konec, prohra ' + ours + ':' + theirs + '. ';
  return '';
}
const TAGS = '#SršniPísek #Sršni #NBL #basketbal';

function headline(h, meta) {
  const tn = meta.teams[h.team].short;
  const p = h.player;
  switch (h.type) {
    case 'run': {
      return {kicker: h.ours ? 'Sršní série' : 'Série soupeře', big: h.pts + ':0',
        title: h.ongoing ? tn + ' jede!' : tn + ' zatáhli',
        sub: 'Série ' + h.pts + ':0' + (h.ongoing ? ' a pořád se počítá' : '') + ' — ' + h.clock};
    }
    case 'comeback':
      return {kicker: 'Obrat', big: '−' + h.deficit + ' ➜ +' + Math.abs(h.s1 - h.s2),
        title: tn + ' otočili zápas', sub: 'Smazané manko ' + h.deficit + ' ' + ptsWord(h.deficit) + ' — ' + h.clock};
    case 'clutch':
      return {kicker: h.deficit ? 'Obrat v závěru' : 'Rozhodující koš', big: p ? '#' + p.shirt : '+' + Math.abs(h.s1 - h.s2),
        title: p ? p.name : tn + ' jdou do vedení',
        sub: tn + ' jdou do vedení' + (h.deficit ? ' po manku ' + h.deficit + ' ' + ptsWord(h.deficit) : '') + ' — ' + h.clock};
    case 'lead':
      return {kicker: h.ours ? 'Náskok' : 'Náskok soupeře', big: '+' + h.lead,
        title: tn + ' utíkají', sub: 'Vedení o ' + h.lead + ' ' + ptsWord(h.lead) + ' — ' + h.clock};
    case 'points':
      return {kicker: h.mark + '+ bodů', big: String(h.line.pts),
        title: p.name, sub: '#' + p.shirt + ' · ' + statLine(h.line) + ' — ' + h.clock};
    case 'threes':
      return {kicker: 'Trojkový mág', big: h.mark + '× 3',
        title: p.name, sub: '#' + p.shirt + ' · ' + h.mark + ' ' + threesWord(h.mark) + ' · ' + h.line.pts + ' ' + ptsWord(h.line.pts) + ' — ' + h.clock};
    case 'doubledouble':
    case 'tripledouble':
      return {kicker: h.type === 'tripledouble' ? 'Triple-double' : 'Double-double',
        big: h.cats.map(c => h.line[c]).join('/'),
        title: p.name, sub: '#' + p.shirt + ' · ' + h.cats.map(c => cat(c, h.line[c])).join(' · ') + ' — ' + h.clock};
    case 'mvp':
      return {kicker: h.final ? 'Hráč zápasu' : 'Zatím nejlepší Sršeň', big: String(p.pts),
        title: p.name, sub: '#' + p.shirt + ' · ' + statLine(p) + ' · EFF ' + p.eff};
  }
  return {kicker: '', big: '', title: '', sub: ''};
}

function caption(h, meta) {
  const tn = meta.teams[h.team].short;
  const p = h.player;
  const sc = scoreLine(h, meta);
  let text;
  switch (h.type) {
    case 'run': {
      text = h.ours
        ? '🐝🔥 Sršní série ' + h.pts + ':0' + '! ' + (h.ongoing ? 'Soupeř nemá odpověď.' : 'Tohle byla jízda.')
        : '⚠️ Soupeř si vzal sérii ' + h.pts + ':0' + '. Sršni, zpátky do toho!';
      break;
    }
    case 'comeback':
      text = h.ours
        ? '🔄🐝 OBRAT! Sršni smazali manko ' + h.deficit + ' ' + ptsWord(h.deficit) + ' a jdou do vedení!'
        : '😬 ' + tn + ' otočili a vedou. Sršni, teď to chce odpověď!';
      break;
    case 'clutch':
      text = h.ours
        ? '🚨 ' + (p ? p.name + ' posílá' : 'Sršni posílají') + ' Sršně do vedení v závěru zápasu!'
          + (h.deficit ? ' Po manku ' + h.deficit + ' ' + ptsWord(h.deficit) + ' je to obrat jako řemen.' : '')
        : '😱 ' + tn + ' jdou do vedení v samotném závěru.';
      break;
    case 'lead':
      text = h.ours ? '🐝📈 Sršni vedou o ' + h.lead + '!' : '📉 Soupeř utíká na +' + h.lead + '.';
      break;
    case 'points':
      text = '🎯 ' + p.name + ' (#' + p.shirt + ') už má ' + h.line.pts + ' ' + ptsWord(h.line.pts) + '!';
      break;
    case 'threes':
      text = '👌 ' + p.name + ' (#' + p.shirt + ') pálí! Už ' + h.mark + ' ' + threesWord(h.mark) + ' v zápase.';
      break;
    case 'doubledouble':
      text = '💪 ' + p.name + ' (#' + p.shirt + ') má double-double: ' + h.cats.map(c => cat(c, h.line[c])).join(' a ') + '.';
      break;
    case 'tripledouble':
      text = '🤯 TRIPLE-DOUBLE! ' + p.name + ' (#' + p.shirt + '): ' + h.cats.map(c => cat(c, h.line[c])).join(', ') + '.';
      break;
    case 'mvp':
      text = (h.final ? resultLine(h, meta) + '🏆 Hráč zápasu: ' : '⭐ Zatím nejlepší Sršeň: ') + p.name + ' (#' + p.shirt + ') — ' + statLine(p) + '.';
      break;
  }
  const when = h.type === 'mvp' && h.final ? 'Konečný stav' : h.clock;
  return text + '\n\n' + when + ' · ' + sc + '\n\n' + TAGS;
}

const api = {analyze, detectOurTeam, isFinished, periodLabel,
  config: {RUN_MIN, COMEBACK_MIN, LEAD_MARKS, PTS_MARKS, THREES_MIN, CLUTCH_SECS}};
globalThis.SrsniHighlights = api;
})();
