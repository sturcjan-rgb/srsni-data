// Testy highlights-engine.js na vymyšleném zápase (bez sítě): node --test
import test from 'node:test';
import assert from 'node:assert/strict';
await import('./highlights-engine.js');
const H = globalThis.SrsniHighlights;

let n = 0;
const pl = (no, first, family) => ({firstName: first, familyName: family, scoreboardName: first[0] + '. ' + family, shirtNumber: no});
// Sršni jsou hosté (tno 2), aby se ověřila detekce týmu
function game(events, finished) {
  let s = [0, 0];
  const pbp = events.map(([period, gt, tno, pno, actionType, success]) => {
    if (success && ['2pt', '3pt', 'freethrow'].includes(actionType)) s[tno - 1] += actionType === '3pt' ? 3 : actionType === '2pt' ? 2 : 1;
    return {actionNumber: ++n, period, gt, tno, pno, actionType, success: success ? 1 : 0, s1: String(s[0]), s2: String(s[1])};
  });
  if (finished) pbp.push({actionNumber: ++n, period: 4, gt: '00:00', tno: 0, pno: 0, actionType: 'game', subType: 'end', s1: String(s[0]), s2: String(s[1])});
  return {
    periodLength: 10,
    tm: {
      '1': {name: 'Soupeř Test', shortName: 'SOUPEŘ', pl: {'1': pl('4', 'Adam', 'Host')}},
      '2': {name: 'Sršni Photomate Písek', shortName: 'Sršni Písek',
        pl: {'1': Object.assign(pl('71', 'Martin', 'Svoboda'), {sPoints: 20, sReboundsTotal: 10, sFieldGoalsAttempted: 12, sFieldGoalsMade: 8}),
             '2': Object.assign(pl('9', 'Matěj', 'Burda'), {sPoints: 6, sFieldGoalsAttempted: 6, sFieldGoalsMade: 3})}}
    },
    pbp: pbp.reverse()   // FIBA posílá nejnovější první
  };
}

test('pozná náš tým podle jména', () => {
  assert.equal(H.detectOurTeam(game([])), 2);
});

test('série bez odpovědi, obrat a náskok', () => {
  const ev = [];
  for (let i = 0; i < 4; i++) ev.push([1, '9:00', 1, 1, '2pt', 1]);        // soupeř 8:0
  for (let i = 0; i < 5; i++) ev.push([2, '8:00', 2, 1, '2pt', 1]);        // Sršni 10:0 → vedou 10:8
  ev.push([2, '5:00', 1, 1, '2pt', 1]);                                    // odpověď soupeře ukončí sérii
  const {highlights} = H.analyze(game(ev));
  const runs = highlights.filter(h => h.type === 'run');
  assert.deepEqual(runs.map(h => [h.team, h.pts, h.ongoing]), [[1, 8, false], [2, 10, false]]);
  const our = runs[1];
  assert.equal(our.s1 + ':' + our.s2, '8:10', 'skóre série je z jejího posledního koše');
  assert.equal(our.big, '10:0');
  const cb = highlights.find(h => h.type === 'comeback');
  assert.ok(cb && cb.ours && cb.deficit === 8);
});

test('probíhající série je označená jako živá', () => {
  const ev = [];
  for (let i = 0; i < 3; i++) ev.push([1, '9:00', 2, 1, '3pt', 1]);        // 9:0, bez odpovědi
  const run = H.analyze(game(ev)).highlights.find(h => h.type === 'run');
  assert.equal(run.ongoing, true);
  assert.match(run.caption, /Sršní série 9:0/);
});

test('milníky hráče a rozhodující koš v závěru', () => {
  const ev = [];
  for (let i = 0; i < 5; i++) ev.push([1, '5:00', 1, 1, '3pt', 1]);        // soupeř vede 15:0
  for (let i = 0; i < 5; i++) ev.push([2, '5:00', 2, 1, '3pt', 1]);        // Svoboda 5 trojek → 15:15
  for (let i = 0; i < 10; i++) ev.push([3, '5:00', 2, 1, 'rebound', 1]);
  ev.push([4, '1:10', 2, 2, '2pt', 1]);                                   // Burda na 15:17 → vedení v závěru
  const {highlights, meta} = H.analyze(game(ev, true));
  const types = highlights.filter(h => h.ours).map(h => h.type);
  assert.ok(types.includes('points'));
  assert.deepEqual(highlights.filter(h => h.type === 'threes' && h.ours).map(h => h.mark), [4, 5]);
  assert.ok(types.includes('doubledouble'));
  const cl = highlights.find(h => h.type === 'clutch');
  assert.equal(cl.player.name, 'Matěj Burda');
  assert.equal(cl.deficit, 15);
  assert.equal(highlights.some(h => h.type === 'comeback'), false, 'obrat v závěru se nehlásí dvakrát');
  const mvp = highlights.find(h => h.type === 'mvp');
  assert.ok(meta.finished && mvp.final);
  assert.equal(mvp.player.name, 'Martin Svoboda');
  assert.match(mvp.caption, /VÝHRA 17:15/);
});

test('čeština v počtech', () => {
  const ev = [[1, '9:00', 2, 1, 'rebound', 1], [1, '9:00', 2, 1, 'assist', 1]];
  for (let i = 0; i < 5; i++) ev.push([1, '8:00', 2, 1, '3pt', 1]);
  const h = H.analyze(game(ev)).highlights.find(h => h.type === 'points');
  assert.match(h.sub, /15 bodů · 1 doskok · 1 asistence/);
});
