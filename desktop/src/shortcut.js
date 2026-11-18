const DEFAULT_SHORTCUT = 'Ctrl+Shift+Space';
const MODIFIER_ORDER = ['Ctrl', 'CommandOrControl', 'Alt', 'Shift', 'Super'];
const MODIFIER_NAMES = {
  ctrl: 'Ctrl',
  control: 'Ctrl',
  commandorcontrol: 'CommandOrControl',
  cmdorctrl: 'CommandOrControl',
  alt: 'Alt',
  option: 'Alt',
  shift: 'Shift',
  super: 'Super',
  meta: 'Super',
};
const NEEDS_ONE_OF = new Set(['Ctrl', 'CommandOrControl', 'Alt', 'Super']);
const NAMED_KEYS = ['Space', 'Tab', 'Enter', 'Up', 'Down', 'Left', 'Right', 'Home', 'End', 'PageUp', 'PageDown', 'Insert', 'Delete'];
const LAST_FUNCTION_KEY = 24;
const MODIFIER_CODES = /^(Shift|Control|Alt|Meta|OS)(Left|Right)$|^AltGraph$/;
const ARROW_CODE = /^Arrow(Up|Down|Left|Right)$/;
const LETTER_CODE = /^Key([A-Z])$/;
const DIGIT_CODE = /^Digit(\d)$/;
const FUNCTION_CODE = /^F(\d{1,2})$/;
const MISSING_MODIFIER = 'Add at least one of Ctrl, Alt or Super (Shift alone is not enough).';

function refuse(error, extra = {}) {
  return { ok: false, error, ...extra };
}

function isKey(part) {
  if (/^[A-Za-z0-9]$/.test(part)) return true;
  const functionKey = /^F(\d{1,2})$/.exec(part);
  if (functionKey) return Number(functionKey[1]) >= 1 && Number(functionKey[1]) <= LAST_FUNCTION_KEY;
  return NAMED_KEYS.includes(part);
}

function canonicalKey(part) {
  if (/^[a-z]$/.test(part)) return part.toUpperCase();
  const named = NAMED_KEYS.find((name) => name.toLowerCase() === part.toLowerCase());
  if (named) return named;
  return part.toUpperCase();
}

function parseAccelerator(input) {
  if (typeof input !== 'string' || input.trim() === '') {
    return refuse('Choose a shortcut.');
  }
  const parts = input.split('+').map((part) => part.trim());
  if (parts.some((part) => part === '')) return refuse('That shortcut is not complete.');
  const modifiers = new Set();
  const keys = [];
  for (const part of parts) {
    const modifier = MODIFIER_NAMES[part.toLowerCase()];
    if (!modifier) {
      keys.push(part);
      continue;
    }
    if (modifiers.has(modifier)) return refuse(`${modifier} appears twice in that shortcut.`);
    modifiers.add(modifier);
  }
  if (keys.length !== 1) return refuse('A shortcut needs exactly one key besides Ctrl, Alt, Shift or Super.');
  if (!isKey(keys[0]) && !isKey(canonicalKey(keys[0]))) return refuse(`"${keys[0]}" is not a key that can be used.`);
  if (![...modifiers].some((modifier) => NEEDS_ONE_OF.has(modifier))) return refuse(MISSING_MODIFIER);
  const ordered = MODIFIER_ORDER.filter((modifier) => modifiers.has(modifier));
  return { ok: true, accelerator: [...ordered, canonicalKey(keys[0])].join('+') };
}

function keyNameFromCode(code) {
  const letter = LETTER_CODE.exec(code);
  if (letter) return letter[1];
  const digit = DIGIT_CODE.exec(code);
  if (digit) return digit[1];
  const functionKey = FUNCTION_CODE.exec(code);
  if (functionKey) return `F${Number(functionKey[1])}`;
  const arrow = ARROW_CODE.exec(code);
  if (arrow) return arrow[1];
  return NAMED_KEYS.includes(code) ? code : null;
}

function acceleratorFromKeyEvent(event) {
  const code = event && typeof event.code === 'string' ? event.code : '';
  if (MODIFIER_CODES.test(code)) return refuse('Now press the key to go with it.', { incomplete: true });
  const key = keyNameFromCode(code);
  if (!key) return refuse('That key cannot be used in a shortcut.');
  const held = [
    event.ctrlKey ? 'Ctrl' : null,
    event.altKey ? 'Alt' : null,
    event.shiftKey ? 'Shift' : null,
    event.metaKey ? 'Super' : null,
  ].filter(Boolean);
  return parseAccelerator([...held, key].join('+'));
}

function registerQuickAsk(globalShortcut, { next, previous, handler }) {
  const parsed = parseAccelerator(next);
  if (!parsed.ok) return parsed;
  const accelerator = parsed.accelerator;
  if (previous) globalShortcut.unregister(previous);
  let registered;
  try {
    registered = globalShortcut.register(accelerator, handler);
  } catch (error) {
    if (previous) globalShortcut.register(previous, handler);
    return refuse(`Could not use ${accelerator}: ${error.message}`);
  }
  if (registered) return { ok: true, accelerator };
  if (previous) globalShortcut.register(previous, handler);
  return refuse(`${accelerator} is already in use by another program. Choose a different shortcut.`);
}

module.exports = { DEFAULT_SHORTCUT, parseAccelerator, acceleratorFromKeyEvent, registerQuickAsk };
