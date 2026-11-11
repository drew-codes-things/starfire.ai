'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '..', '..', 'static');
const PAGES = ['index.html', 'login.html', 'setup.html'];
const DARK_BACKGROUND = '#19191c';
const LIGHT_BACKGROUND = '#ffffff';

function inlineThemeScript(page) {
  const head = fs.readFileSync(path.join(STATIC, page), 'utf8').split('</head>')[0];
  const match = head.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(match, `${page} has no inline theme script in its head`);
  return match[1];
}

function runThemeScript(page, { settings = {}, systemLight = false, width = 1440, storageBroken = false } = {}) {
  const classes = new Set();
  const meta = { content: DARK_BACKGROUND, setAttribute(name, value) { this[name] = value; } };
  const root = {
    classList: {
      add: name => classes.add(name),
      toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)),
    },
  };
  const context = {
    document: { documentElement: root, querySelector: () => meta },
    localStorage: {
      getItem(key) {
        if (storageBroken) throw new Error('storage is blocked');
        return key in settings ? settings[key] : null;
      },
    },
    window: { innerWidth: width, matchMedia: () => ({ matches: systemLight }) },
    getComputedStyle: () => ({
      getPropertyValue: () => (classes.has('light') ? ` ${LIGHT_BACKGROUND}` : ` ${DARK_BACKGROUND}`),
    }),
  };
  vm.runInNewContext(inlineThemeScript(page), context);
  return { classes, themeColour: meta.content };
}

for (const page of PAGES) {
  test(`${page}: a saved light theme is applied before paint`, () => {
    const { classes, themeColour } = runThemeScript(page, { settings: { sf_theme: 'light' } });
    assert.equal(classes.has('light'), true);
    assert.equal(themeColour, LIGHT_BACKGROUND);
  });

  test(`${page}: dark is the default and the old light setting still works`, () => {
    assert.equal(runThemeScript(page).classes.has('light'), false);
    assert.equal(runThemeScript(page).themeColour, DARK_BACKGROUND);
    assert.equal(runThemeScript(page, { settings: { sf_light: '1' } }).classes.has('light'), true);
  });

  test(`${page}: system follows the operating system`, () => {
    const settings = { sf_theme: 'system' };
    assert.equal(runThemeScript(page, { settings, systemLight: true }).classes.has('light'), true);
    assert.equal(runThemeScript(page, { settings, systemLight: false }).classes.has('light'), false);
  });

  test(`${page}: blocked storage falls back to dark without throwing`, () => {
    const { classes } = runThemeScript(page, { storageBroken: true, systemLight: true });
    assert.equal(classes.has('light'), false);
  });

  test(`${page}: the sidebar starts closed on phones and when it was left closed`, () => {
    assert.equal(runThemeScript(page, { width: 390 }).classes.has('sidebar-closed'), true);
    assert.equal(runThemeScript(page, { settings: { sf_sidebar_closed: '1' } }).classes.has('sidebar-closed'), true);
    assert.equal(runThemeScript(page).classes.has('sidebar-closed'), false);
  });
}
