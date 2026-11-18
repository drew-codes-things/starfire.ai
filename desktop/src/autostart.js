const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const HIDDEN_FLAG = '--hidden';
const AUTOSTART_FILE_NAME = 'starfire-desktop.desktop';
const UNSAFE_PATH_CHARACTERS = /[\r\n\0]/;

function quoteExecArgument(value) {
  const quoted = value.replace(/[\\"`$]/g, '\\$&');
  return `"${quoted.replace(/\\/g, '\\\\')}"`;
}

function autostartEntry({ execPath }) {
  const isUsable = typeof execPath === 'string' && execPath.startsWith('/') && !UNSAFE_PATH_CHARACTERS.test(execPath);
  if (!isUsable) throw new Error('The program path for autostart must be an absolute path on one line.');
  return [
    '[Desktop Entry]',
    'Type=Application',
    'Name=starfire',
    'Comment=starfire.ai in its own window',
    `Exec=${quoteExecArgument(execPath)} ${HIDDEN_FLAG}`,
    'Icon=starfire',
    'Terminal=false',
    'X-GNOME-Autostart-enabled=true',
    '',
  ].join('\n');
}

function autostartFilePath(env, home) {
  const configured = env.XDG_CONFIG_HOME;
  const base = configured && path.isAbsolute(configured) ? configured : path.join(home, '.config');
  return path.join(base, 'autostart', AUTOSTART_FILE_NAME);
}

function applyAutostart({ enabled, platform, fsImpl = fs, env = process.env, home = os.homedir(), execPath, app }) {
  if (platform !== 'linux') {
    app.setLoginItemSettings({ openAtLogin: enabled, args: [HIDDEN_FLAG] });
    return;
  }
  const file = autostartFilePath(env, home);
  if (!enabled) {
    fsImpl.rmSync(file, { force: true });
    return;
  }
  const entry = autostartEntry({ execPath });
  fsImpl.mkdirSync(path.dirname(file), { recursive: true });
  fsImpl.writeFileSync(file, entry, { mode: 0o644 });
}

module.exports = { autostartEntry, autostartFilePath, applyAutostart, HIDDEN_FLAG };
