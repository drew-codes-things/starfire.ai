const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { autostartEntry, autostartFilePath, applyAutostart, HIDDEN_FLAG } = require('../src/autostart');

test('the entry starts the app hidden in the tray', () => {
  const entry = autostartEntry({ execPath: '/home/me/Applications/starfire.AppImage' });
  assert.match(entry, /^\[Desktop Entry\]\n/);
  assert.match(entry, /^Type=Application$/m);
  assert.match(entry, /^Name=starfire$/m);
  assert.ok(entry.split('\n').includes(`Exec="/home/me/Applications/starfire.AppImage" ${HIDDEN_FLAG}`));
  assert.match(entry, /^X-GNOME-Autostart-enabled=true$/m);
  assert.match(entry, /^Terminal=false$/m);
});

test('a path with spaces or quotes is escaped for the Exec line', () => {
  const entry = autostartEntry({ execPath: '/home/me/My Apps/star"fire.AppImage' });
  assert.ok(entry.split('\n').includes(`Exec="/home/me/My Apps/star\\\\"fire.AppImage" ${HIDDEN_FLAG}`));
});

test('a path that could add lines to the entry is refused', () => {
  assert.throws(() => autostartEntry({ execPath: '/a\nExec=evil' }), /path/i);
  assert.throws(() => autostartEntry({ execPath: '' }), /path/i);
  assert.throws(() => autostartEntry({ execPath: 'relative/path' }), /path/i);
});

test('the autostart file lives in the XDG config folder', () => {
  assert.equal(autostartFilePath({ XDG_CONFIG_HOME: '/x/config' }, '/home/me'), path.join('/x/config', 'autostart', 'starfire-desktop.desktop'));
  assert.equal(autostartFilePath({}, '/home/me'), path.join('/home/me', '.config', 'autostart', 'starfire-desktop.desktop'));
  assert.equal(autostartFilePath({ XDG_CONFIG_HOME: 'relative' }, '/home/me'), path.join('/home/me', '.config', 'autostart', 'starfire-desktop.desktop'));
});

function fakeFs() {
  const files = new Map();
  return {
    files,
    mkdirSync() {},
    writeFileSync(file, content) { files.set(file, content); },
    rmSync(file) { files.delete(file); },
    existsSync(file) { return files.has(file); },
  };
}

test('turning it on writes the entry and turning it off removes it', () => {
  const fsImpl = fakeFs();
  const file = path.join('/home/me', '.config', 'autostart', 'starfire-desktop.desktop');
  applyAutostart({ enabled: true, platform: 'linux', fsImpl, env: {}, home: '/home/me', execPath: '/opt/starfire' });
  assert.match(fsImpl.files.get(file), /Exec="\/opt\/starfire" --hidden/);
  applyAutostart({ enabled: false, platform: 'linux', fsImpl, env: {}, home: '/home/me', execPath: '/opt/starfire' });
  assert.equal(fsImpl.files.has(file), false);
});

test('turning it off when it was never on does nothing and does not fail', () => {
  const fsImpl = fakeFs();
  applyAutostart({ enabled: false, platform: 'linux', fsImpl, env: {}, home: '/home/me', execPath: '/opt/starfire' });
  assert.equal(fsImpl.files.size, 0);
});

test('on Windows and macOS the login item setting is used', () => {
  const fsImpl = fakeFs();
  const calls = [];
  const app = { setLoginItemSettings: (settings) => calls.push(settings) };
  applyAutostart({ enabled: true, platform: 'win32', fsImpl, app });
  assert.deepEqual(calls, [{ openAtLogin: true, args: [HIDDEN_FLAG] }]);
  assert.equal(fsImpl.files.size, 0);
});
