const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

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

class FakeElement {
  constructor(tag, id = '') {
    this.tag = tag;
    this.id = id;
    this.children = [];
    this.textContent = '';
    this.className = '';
    this.hidden = false;
    this.checked = false;
    this.disabled = false;
    this.value = '';
    this.dataset = {};
    this.listeners = {};
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  set innerHTML(value) { throw new Error(`innerHTML was set to ${value}`); }
}

function textOf(element) {
  return [element.textContent, ...element.children.map(textOf)].join('');
}

async function openSettingsPage(linkState) {
  const elements = new Map();
  const document = {
    hidden: false,
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, new FakeElement('div', id));
      return elements.get(id);
    },
    createElement: (tag) => new FakeElement(tag),
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  let currentState = linkState;
  const bridge = {
    getSettings: async () => ({ settings: { shortcut: 'Ctrl+Shift+Space', address: 'https://starfire.example.com', startWithSystem: false, notifications: true, closeToTray: true, artyLink: true }, shortcutProblem: null }),
    linkStatus: async () => currentState,
  };
  const timers = [];
  const context = { window: { starfireDesktop: bridge }, document, setInterval: (callback) => timers.push(callback) };
  vm.runInNewContext(script, context);
  await new Promise((resolve) => setImmediate(resolve));
  return {
    element: (id) => document.getElementById(id),
    list: document.getElementById('capability-list'),
    panel: document.getElementById('link-capabilities'),
    refresh: async (state) => { currentState = state; await timers[0](); },
  };
}

const CONNECTED = {
  state: 'connected', paired: true, computer: true, artyDesktop: '1.6.0',
  capabilities: [
    { id: 'screen.look', version: 1, available: true },
    { id: 'browser.open', version: 1, available: true, browsers: [{ name: 'Firefox', state: 'connected' }, { name: 'Chrome 2', state: 'connected' }] },
    { id: 'browser.own', version: 1, available: false },
    { id: 'screen.control', version: 1, available: false },
    { id: 'quiz', version: 1, available: true },
  ],
};

test('settings has a capabilities panel that starts hidden', () => {
  assert.match(html, /id="link-capabilities"[^>]*\bhidden\b/);
  assert.match(html, /id="capability-list"/);
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|outerHTML/);
});

test('the capabilities panel lists each capability with a plain label, the browsers and whether it is available', async () => {
  const page = await openSettingsPage(CONNECTED);
  assert.equal(page.panel.hidden, false);
  assert.deepEqual(page.list.children.map(textOf), [
    'Look at your screenavailable',
    'Open pages in your browsers (Firefox, Chrome 2)available',
    "Arty's own browsernot available",
    'Use your mouse and keyboardnot available',
    'Quiz helperavailable',
  ]);
});

test('the capabilities panel follows the link status as it refreshes', async () => {
  const page = await openSettingsPage({ state: 'unreachable', paired: true, capabilities: [], artyDesktop: '' });
  assert.equal(page.panel.hidden, true);
  assert.equal(page.list.children.length, 0);
  await page.refresh(CONNECTED);
  assert.equal(page.panel.hidden, false);
  assert.equal(page.list.children.length, 5);
  await page.refresh({ ...CONNECTED, capabilities: [{ id: 'browser.open', version: 1, available: false, browsers: [] }] });
  assert.deepEqual(page.list.children.map(textOf), ['Open pages in your browsersnot available']);
  await page.refresh({ state: 'off', paired: true, capabilities: [], artyDesktop: '' });
  assert.equal(page.panel.hidden, true);
  assert.equal(page.list.children.length, 0);
});

test('a capability the page has no label for is not shown, and names arrive only as text', async () => {
  const page = await openSettingsPage({ ...CONNECTED, capabilities: [{ id: 'shell.run', version: 1, available: true }, { id: 'browser.open', version: 1, available: true, browsers: [{ name: '<img src=x onerror=alert(1)>', state: 'connected' }] }] });
  assert.deepEqual(page.list.children.map(textOf), ['Open pages in your browsers (<img src=x onerror=alert(1)>)available']);
});

test('settings has a switch for linking by itself', () => {
  assert.match(html, /<input id="artyAutoLink" type="checkbox"/);
  assert.match(script, /'artyAutoLink'/);
  assert.match(html, /id="link-paired-title"/);
});

test('the paired row says the link was made by itself only when it was', async () => {
  const page = await openSettingsPage({ ...CONNECTED, linkedByItself: true });
  assert.equal(page.element('link-paired-title').textContent, 'Linked to Arty Desktop (it linked by itself)');
  await page.refresh({ ...CONNECTED, linkedByItself: false });
  assert.equal(page.element('link-paired-title').textContent, 'Linked to Arty Desktop');
  await page.refresh({ state: 'unpaired', paired: false, capabilities: [], artyDesktop: '', linkedByItself: false });
  assert.equal(page.element('link-pairing').hidden, false);
  assert.match(page.element('link-pairing-help').textContent, /^Not linked\./);
});
