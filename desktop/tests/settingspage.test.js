const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const pages = path.join(__dirname, '..', 'pages');
const html = fs.readFileSync(path.join(pages, 'settings.html'), 'utf8');
const script = fs.readFileSync(path.join(pages, 'settings.js'), 'utf8');
const styles = fs.readFileSync(path.join(pages, 'pages.css'), 'utf8');
const preload = fs.readFileSync(path.join(__dirname, '..', 'preload.js'), 'utf8');

test('settings has the Arty Desktop switch, a light with words beside it, and the pairing rows', () => {
  assert.match(html, /<input id="artyLink" type="checkbox"/);
  assert.match(html, /id="link-light"[^>]*data-state="off"/);
  assert.match(html, /id="link-label"/);
  for (const id of ['link-pairing', 'link-code', 'link-pair', 'link-paired', 'link-test', 'link-unpair']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test('the pairing and paired rows start hidden and the stylesheet can hide them', () => {
  assert.match(html, /id="link-pairing"[^>]*\bhidden\b/);
  assert.match(html, /id="link-paired"[^>]*\bhidden\b/);
  assert.match(styles, /\[hidden\] \{ display: none !important; \}/);
});

test('the light has a colour for every state the link can report', () => {
  for (const state of ['off', 'unpaired', 'unreachable', 'unproven', 'connected']) {
    assert.match(styles, new RegExp(`\\.light\\[data-state="${state}"\\]`));
  }
});

test('the page polls the link while it is open and uses only the link bridge calls', () => {
  assert.match(script, /setInterval\(refreshLink/);
  for (const call of ['linkStatus', 'linkPair', 'linkUnpair', 'linkTry']) {
    assert.ok(script.includes(`bridge.${call}(`), call);
    assert.ok(preload.includes(`${call}:`), `${call} in the preload`);
  }
});

test('the label says Connected only for the connected state', () => {
  assert.match(script, /connected: 'Connected'/);
  assert.equal((script.match(/'Connected'/g) || []).length, 1);
});

test('the code field is wiped after pairing', () => {
  assert.match(script, /linkCode\.value = ''/);
});
