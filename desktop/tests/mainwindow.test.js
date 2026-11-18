const test = require('node:test');
const assert = require('node:assert/strict');
const { createWindowManager } = require('../src/windows');
const { DEFAULT_SETTINGS } = require('../src/settings');

const ADDRESS = 'https://starfire.example.com';

function build() {
  const windows = [];
  class FakeWindow {
    constructor(options) {
      this.options = options;
      this.reloads = 0;
      this.webContents = { on() {}, setWindowOpenHandler() {}, loadURL() {}, reload: () => { this.reloads += 1; }, send() {} };
      windows.push(this);
    }
    on() {}
    loadURL() {}
    loadFile() {}
    removeMenu() {}
    show() {}
    focus() {}
    hide() {}
    isMinimized() { return false; }
    isVisible() { return true; }
    isFocused() { return true; }
    close() {}
  }
  const electron = { BrowserWindow: FakeWindow, Notification: { isSupported: () => false }, screen: {}, shell: {}, app: { quit() {} } };
  const controller = { get: () => ({ ...DEFAULT_SETTINGS, address: ADDRESS, closeToTray: true }) };
  const manager = createWindowManager({ electron, controller, pagesDir: '/app/pages', preloadPath: '/app/preload.js', webPreloadPath: '/app/preload-web.js', iconPath: '/app/icon.png' });
  return { manager, windows };
}

test('the starfire window gets the narrow page bridge preload and nothing weaker', () => {
  const { manager, windows } = build();
  manager.showMain();
  const preferences = windows[0].options.webPreferences;
  assert.equal(preferences.preload, '/app/preload-web.js');
  assert.equal(preferences.partition, 'persist:starfire');
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.nodeIntegration, false);
});

test('the app\'s own pages keep their own preload and the starfire window is the only one with the page bridge', () => {
  const { manager, windows } = build();
  manager.showMain();
  manager.showSettings();
  manager.showConnect('first-run');
  const preloads = windows.map((window) => window.options.webPreferences.preload);
  assert.deepEqual(preloads, ['/app/preload-web.js', '/app/preload.js', '/app/preload.js']);
});

test('the window can say whether a sender is the starfire window, and can reload it for a changed link', () => {
  const { manager, windows } = build();
  assert.equal(manager.isMainContents(windows[0] && windows[0].webContents), false);
  manager.showMain();
  assert.equal(manager.isMainContents(windows[0].webContents), true);
  assert.equal(manager.isMainContents({}), false);
  assert.equal(manager.isMainContents(undefined), false);
  manager.reloadMain();
  assert.equal(windows[0].reloads, 1);
});

test('reloading with no starfire window open does nothing', () => {
  const { manager } = build();
  assert.doesNotThrow(() => manager.reloadMain());
});
