const fs = require('node:fs');
const path = require('node:path');
const { normalizeAddress } = require('./address');
const { DEFAULT_SHORTCUT, parseAccelerator } = require('./shortcut');

const DEFAULT_SETTINGS = Object.freeze({
  address: '',
  allowInsecure: false,
  shortcut: DEFAULT_SHORTCUT,
  startWithSystem: false,
  notifications: true,
  closeToTray: true,
});
const BOOLEAN_KEYS = ['allowInsecure', 'startWithSystem', 'notifications', 'closeToTray'];
const PRIVATE_FILE_MODE = 0o600;

function booleanOrDefault(value, key) {
  return typeof value === 'boolean' ? value : DEFAULT_SETTINGS[key];
}

function normalizeStored(raw) {
  const source = raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const settings = { ...DEFAULT_SETTINGS };
  for (const key of BOOLEAN_KEYS) settings[key] = booleanOrDefault(source[key], key);
  const shortcut = parseAccelerator(source.shortcut);
  if (shortcut.ok) settings.shortcut = shortcut.accelerator;
  if (typeof source.address === 'string' && source.address !== '') {
    const address = normalizeAddress(source.address, { allowInsecure: settings.allowInsecure });
    if (address.ok) settings.address = address.origin;
  }
  return settings;
}

function loadSettings(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return { settings: { ...DEFAULT_SETTINGS }, warning: null };
    return { settings: { ...DEFAULT_SETTINGS }, warning: `Could not read the config file (${error.message}); using the defaults.` };
  }
  try {
    return { settings: normalizeStored(JSON.parse(text)), warning: null };
  } catch (error) {
    return { settings: { ...DEFAULT_SETTINGS }, warning: `The config file is not valid (${error.message}); using the defaults.` };
  }
}

function saveSettings(file, settings) {
  const temporary = `${file}.${process.pid}.tmp`;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(settings, null, 2)}\n`, { mode: PRIVATE_FILE_MODE });
  fs.renameSync(temporary, file);
}

function refuse(error) {
  return { ok: false, error };
}

function applySettingsPatch(current, patch) {
  if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) return refuse('Nothing to change.');
  const unknown = Object.keys(patch).filter((key) => !(key in DEFAULT_SETTINGS));
  if (unknown.length > 0) return refuse(`Unknown setting: ${unknown.join(', ')}.`);
  const merged = { ...current };
  for (const key of BOOLEAN_KEYS) {
    if (!(key in patch)) continue;
    if (typeof patch[key] !== 'boolean') return refuse(`${key} must be on or off.`);
    merged[key] = patch[key];
  }
  if ('shortcut' in patch) {
    const shortcut = parseAccelerator(patch.shortcut);
    if (!shortcut.ok) return refuse(`Shortcut: ${shortcut.error}`);
    merged.shortcut = shortcut.accelerator;
  }
  if ('address' in patch && typeof patch.address !== 'string') return refuse('The address must be text.');
  if ('address' in patch) merged.address = patch.address.trim();
  if (merged.address !== '') {
    const address = normalizeAddress(merged.address, { allowInsecure: merged.allowInsecure });
    if (!address.ok) return refuse(address.error);
    merged.address = address.origin;
  }
  return { ok: true, settings: merged };
}

module.exports = { DEFAULT_SETTINGS, loadSettings, saveSettings, applySettingsPatch };
