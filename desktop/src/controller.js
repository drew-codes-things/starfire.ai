const { normalizeAddress, checkStarfire } = require('./address');
const { applySettingsPatch, saveSettings, withArtyToken } = require('./settings');
const { registerQuickAsk } = require('./shortcut');

function refuse(error) {
  return { ok: false, error };
}

function createSettingsController({ file, initial, globalShortcut, onQuickAsk, setAutostart, onChange, check = checkStarfire }) {
  let current = { ...initial };
  let activeShortcut = null;
  let shortcutProblem = null;

  function get() {
    return { ...current };
  }

  function start() {
    const registered = registerQuickAsk(globalShortcut, { next: current.shortcut, previous: null, handler: onQuickAsk });
    if (registered.ok) activeShortcut = registered.accelerator;
    shortcutProblem = registered.ok ? null : registered.error;
    return registered;
  }

  function applyAutostartChange(next, previous) {
    if (next.startWithSystem === previous.startWithSystem) return { ok: true };
    try {
      setAutostart(next.startWithSystem);
    } catch (error) {
      return refuse(`Could not change start with the PC: ${error.message}`);
    }
    return { ok: true };
  }

  function applyShortcutChange(next, patch) {
    const isWanted = 'shortcut' in patch && next.shortcut !== activeShortcut;
    if (!isWanted) return { ok: true };
    const registered = registerQuickAsk(globalShortcut, { next: next.shortcut, previous: activeShortcut, handler: onQuickAsk });
    if (registered.ok) {
      activeShortcut = registered.accelerator;
      shortcutProblem = null;
    }
    return registered;
  }

  function revertAutostart(previous) {
    try {
      setAutostart(previous.startWithSystem);
    } catch (error) {
      return;
    }
  }

  function update(patch) {
    const result = applySettingsPatch(current, patch);
    if (!result.ok) return result;
    const next = result.settings;
    const previous = current;
    const autostart = applyAutostartChange(next, previous);
    if (!autostart.ok) return autostart;
    const shortcut = applyShortcutChange(next, patch);
    if (!shortcut.ok) {
      if (next.startWithSystem !== previous.startWithSystem) revertAutostart(previous);
      return shortcut;
    }
    try {
      saveSettings(file, next);
    } catch (error) {
      return refuse(`Could not save the settings: ${error.message}`);
    }
    current = next;
    onChange(get(), previous);
    return { ok: true, settings: get() };
  }

  function setArtyToken(token) {
    const result = withArtyToken(current, token);
    if (!result.ok) return result;
    try {
      saveSettings(file, result.settings);
    } catch (error) {
      return refuse(`Could not save the settings: ${error.message}`);
    }
    const previous = current;
    current = result.settings;
    onChange(get(), previous);
    return { ok: true };
  }

  async function connect({ address, allowInsecure }) {
    const insecure = allowInsecure === true;
    const normalized = normalizeAddress(address, { allowInsecure: insecure });
    if (!normalized.ok) return normalized;
    const health = await check(normalized.origin);
    if (!health.ok) return health;
    const saved = update({ address: normalized.origin, allowInsecure: insecure });
    if (!saved.ok) return saved;
    return { ok: true, origin: normalized.origin, version: health.version };
  }

  return { get, start, update, connect, setArtyToken, shortcutProblem: () => shortcutProblem };
}

module.exports = { createSettingsController };
