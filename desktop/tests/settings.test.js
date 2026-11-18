const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DEFAULT_SETTINGS, loadSettings, saveSettings, applySettingsPatch } = require('../src/settings');

function scratchFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sfd-settings-'));
  return { dir, file: path.join(dir, 'config.json') };
}

test('defaults are no address, https only, Ctrl+Shift+Space, notifications on, no autostart', () => {
  assert.deepEqual(DEFAULT_SETTINGS, {
    address: '',
    allowInsecure: false,
    shortcut: 'Ctrl+Shift+Space',
    startWithSystem: false,
    notifications: true,
    closeToTray: true,
  });
});

test('a missing file gives the defaults without a warning', () => {
  const { dir, file } = scratchFile();
  const result = loadSettings(file);
  assert.deepEqual(result.settings, DEFAULT_SETTINGS);
  assert.equal(result.warning, null);
  fs.rmSync(dir, { recursive: true });
});

test('a corrupt file gives the defaults and says why', () => {
  const { dir, file } = scratchFile();
  fs.writeFileSync(file, '{not json');
  const result = loadSettings(file);
  assert.deepEqual(result.settings, DEFAULT_SETTINGS);
  assert.match(result.warning, /config/i);
  fs.rmSync(dir, { recursive: true });
});

test('saving then loading returns the same settings, privately and without leftovers', () => {
  const { dir, file } = scratchFile();
  const settings = { ...DEFAULT_SETTINGS, address: 'https://starfire.example.com', shortcut: 'Ctrl+Alt+K', startWithSystem: true };
  saveSettings(file, settings);
  assert.deepEqual(loadSettings(file).settings, settings);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.deepEqual(fs.readdirSync(dir), ['config.json']);
  fs.rmSync(dir, { recursive: true });
});

test('invalid stored values fall back one by one and unknown keys are dropped', () => {
  const { dir, file } = scratchFile();
  fs.writeFileSync(file, JSON.stringify({
    address: 'http://starfire.example.com',
    allowInsecure: 'yes',
    shortcut: 'banana',
    startWithSystem: true,
    notifications: 0,
    closeToTray: false,
    evil: '__proto__',
  }));
  const { settings } = loadSettings(file);
  assert.equal(settings.address, '');
  assert.equal(settings.allowInsecure, false);
  assert.equal(settings.shortcut, DEFAULT_SETTINGS.shortcut);
  assert.equal(settings.startWithSystem, true);
  assert.equal(settings.notifications, true);
  assert.equal(settings.closeToTray, false);
  assert.equal('evil' in settings, false);
  fs.rmSync(dir, { recursive: true });
});

test('a stored http LAN address only survives with the tick box', () => {
  const { dir, file } = scratchFile();
  fs.writeFileSync(file, JSON.stringify({ address: 'http://192.168.77.5:8080', allowInsecure: true }));
  assert.equal(loadSettings(file).settings.address, 'http://192.168.77.5:8080');
  fs.writeFileSync(file, JSON.stringify({ address: 'http://192.168.77.5:8080', allowInsecure: false }));
  assert.equal(loadSettings(file).settings.address, '');
  fs.rmSync(dir, { recursive: true });
});

test('a patch changes only what it names and normalises the values', () => {
  const current = { ...DEFAULT_SETTINGS, address: 'https://starfire.example.com' };
  const result = applySettingsPatch(current, { shortcut: 'ctrl+alt+k', notifications: false });
  assert.equal(result.ok, true);
  assert.equal(result.settings.shortcut, 'Ctrl+Alt+K');
  assert.equal(result.settings.notifications, false);
  assert.equal(result.settings.address, 'https://starfire.example.com');
  assert.equal(current.shortcut, 'Ctrl+Shift+Space');
});

test('a patch with a bad shortcut or address is refused whole', () => {
  const current = { ...DEFAULT_SETTINGS, address: 'https://starfire.example.com' };
  const badShortcut = applySettingsPatch(current, { shortcut: 'Space', notifications: false });
  assert.equal(badShortcut.ok, false);
  assert.match(badShortcut.error, /shortcut/i);
  const badAddress = applySettingsPatch(current, { address: 'http://public.example.com' });
  assert.equal(badAddress.ok, false);
  const wrongType = applySettingsPatch(current, { startWithSystem: 'yes' });
  assert.equal(wrongType.ok, false);
  const unknown = applySettingsPatch(current, { surprise: true });
  assert.equal(unknown.ok, false);
});

test('a new address is stored as its origin, http needs the tick box in the same patch or already', () => {
  const current = { ...DEFAULT_SETTINGS };
  assert.equal(applySettingsPatch(current, { address: 'https://a.example.com/path' }).settings.address, 'https://a.example.com');
  assert.equal(applySettingsPatch(current, { address: 'http://10.0.0.2:8080' }).ok, false);
  const ticked = applySettingsPatch(current, { address: 'http://10.0.0.2:8080', allowInsecure: true });
  assert.equal(ticked.ok, true);
  assert.equal(ticked.settings.address, 'http://10.0.0.2:8080');
});

test('clearing the tick box while the address is http is refused', () => {
  const current = { ...DEFAULT_SETTINGS, address: 'http://10.0.0.2:8080', allowInsecure: true };
  const result = applySettingsPatch(current, { allowInsecure: false });
  assert.equal(result.ok, false);
});
