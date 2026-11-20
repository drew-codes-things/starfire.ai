const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DEFAULT_SETTINGS, loadSettings, saveSettings, applySettingsPatch, publicSettings, withArtyToken } = require('../src/settings');

const NO_UNIX_PERMISSIONS = process.platform === 'win32' && 'Windows has no Unix permission bits';

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
    artyLink: false,
    artyAutoLink: true,
    artyLinkedByItself: false,
    artyToken: '',
    artyPort: 8765,
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

test('saving then loading returns the same settings, privately and without leftovers', { skip: NO_UNIX_PERMISSIONS }, () => {
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

const GOOD_TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';

test('the Arty link is off by default and a stored token only loads when it looks like one', () => {
  const { dir, file } = scratchFile();
  fs.writeFileSync(file, JSON.stringify({ artyLink: true, artyToken: GOOD_TOKEN, artyPort: 8800 }));
  const { settings } = loadSettings(file);
  assert.equal(settings.artyLink, true);
  assert.equal(settings.artyToken, GOOD_TOKEN);
  assert.equal(settings.artyPort, 8800);
  for (const bad of ['', 'short', 'has spaces in it and more more more more', 'x'.repeat(300), 42, null, ['a']]) {
    fs.writeFileSync(file, JSON.stringify({ artyToken: bad }));
    assert.equal(loadSettings(file).settings.artyToken, '', String(bad));
  }
  fs.rmSync(dir, { recursive: true });
});

test('a stored port must be a whole number from 1024 up', () => {
  const { dir, file } = scratchFile();
  for (const bad of [80, 1023, 65536, 8765.5, '8765', null, -1, Number.NaN]) {
    fs.writeFileSync(file, JSON.stringify({ artyPort: bad }));
    assert.equal(loadSettings(file).settings.artyPort, 8765, String(bad));
  }
  fs.rmSync(dir, { recursive: true });
});

test('the saved file holds the token privately', { skip: NO_UNIX_PERMISSIONS }, () => {
  const { dir, file } = scratchFile();
  saveSettings(file, { ...DEFAULT_SETTINGS, artyLink: true, artyToken: GOOD_TOKEN });
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.equal(loadSettings(file).settings.artyToken, GOOD_TOKEN);
  fs.rmSync(dir, { recursive: true });
});

test('a patch can switch the link but never set the token or the port', () => {
  const current = { ...DEFAULT_SETTINGS };
  assert.equal(applySettingsPatch(current, { artyLink: true }).settings.artyLink, true);
  assert.equal(applySettingsPatch(current, { artyLink: 'yes' }).ok, false);
  const token = applySettingsPatch(current, { artyToken: GOOD_TOKEN });
  assert.equal(token.ok, false);
  assert.match(token.error, /artyToken/);
  assert.equal(applySettingsPatch(current, { artyPort: 9000 }).ok, false);
});

test('the pages see whether Arty is paired but never the token', () => {
  const shown = publicSettings({ ...DEFAULT_SETTINGS, artyToken: GOOD_TOKEN, artyLink: true });
  assert.equal(shown.artyPaired, true);
  assert.equal('artyToken' in shown, false);
  assert.equal(JSON.stringify(shown).includes(GOOD_TOKEN), false);
  assert.equal(publicSettings({ ...DEFAULT_SETTINGS }).artyPaired, false);
});

test('a new token is checked, and an empty one forgets the pairing', () => {
  const current = { ...DEFAULT_SETTINGS, artyToken: GOOD_TOKEN };
  assert.equal(withArtyToken(current, 'nope').ok, false);
  assert.equal(withArtyToken(current, '').settings.artyToken, '');
  assert.equal(withArtyToken({ ...DEFAULT_SETTINGS }, GOOD_TOKEN).settings.artyToken, GOOD_TOKEN);
});

test('linking by itself can be switched, but whether a link was made by itself cannot be set from a page', () => {
  const current = { ...DEFAULT_SETTINGS };
  assert.equal(applySettingsPatch(current, { artyAutoLink: false }).settings.artyAutoLink, false);
  assert.equal(applySettingsPatch(current, { artyAutoLink: 'no' }).ok, false);
  const marked = applySettingsPatch(current, { artyLinkedByItself: true });
  assert.equal(marked.ok, false);
  assert.match(marked.error, /artyLinkedByItself/);
});

test('a stored link-by-itself switch that is not a boolean falls back to on', () => {
  const { dir, file } = scratchFile();
  for (const bad of ['false', 0, null]) {
    fs.writeFileSync(file, JSON.stringify({ artyAutoLink: bad, artyLinkedByItself: 'yes' }));
    const { settings } = loadSettings(file);
    assert.equal(settings.artyAutoLink, true, String(bad));
    assert.equal(settings.artyLinkedByItself, false);
  }
  fs.writeFileSync(file, JSON.stringify({ artyAutoLink: false, artyLinkedByItself: true }));
  assert.equal(loadSettings(file).settings.artyAutoLink, false);
  assert.equal(loadSettings(file).settings.artyLinkedByItself, true);
  fs.rmSync(dir, { recursive: true });
});

test('a token says whether it came from linking by itself', () => {
  const byItself = withArtyToken({ ...DEFAULT_SETTINGS }, GOOD_TOKEN, { byItself: true }).settings;
  assert.equal(byItself.artyLinkedByItself, true);
  assert.equal(withArtyToken({ ...DEFAULT_SETTINGS, artyLinkedByItself: true }, GOOD_TOKEN).settings.artyLinkedByItself, false);
  assert.equal(withArtyToken({ ...DEFAULT_SETTINGS }, GOOD_TOKEN, { byItself: 'yes' }).settings.artyLinkedByItself, false);
});

test('forgetting the pairing also stops linking by itself, so Unpair does not link straight back', () => {
  const current = { ...DEFAULT_SETTINGS, artyToken: GOOD_TOKEN, artyAutoLink: true, artyLinkedByItself: true };
  const forgotten = withArtyToken(current, '').settings;
  assert.equal(forgotten.artyToken, '');
  assert.equal(forgotten.artyAutoLink, false);
  assert.equal(forgotten.artyLinkedByItself, false);
});
