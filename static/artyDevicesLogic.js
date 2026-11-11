'use strict';

const QR_BORDER = 4;
const QR_ERROR_CORRECTION = 'M';
const QR_PICK_SIZE_TO_FIT = 0;
const CODE_HALF = 4;
const CODE_LETTERS = /^[A-HJ-NP-Z2-9]{8}$/;
const SECONDS_PER_MINUTE = 60;
const MILLISECONDS_PER_SECOND = 1000;
const UNNAMED_DEVICE = 'A device';

function lettersOf(code) {
  return String(code ?? '').toUpperCase().replace(/[\s-]/g, '');
}

function normalisedCode(raw) {
  const letters = lettersOf(raw);
  if (!CODE_LETTERS.test(letters)) return null;
  return `${letters.slice(0, CODE_HALF)}-${letters.slice(CODE_HALF)}`;
}

function codesMatch(expected, typed) {
  const wanted = lettersOf(expected);
  return wanted.length > 0 && lettersOf(typed) === wanted;
}

function pendingSummary(pending) {
  const name = `“${String(pending?.name || UNNAMED_DEVICE)}”`;
  const minutes = Math.round(Number(pending?.expires_in) / SECONDS_PER_MINUTE);
  const deadline = minutes > 0 ? `in ${minutes} minute${minutes === 1 ? '' : 's'}` : 'soon';
  const asked = pending?.address ? `${name} asked from ${pending.address}.` : `${name} is waiting.`;
  return `${asked} The code runs out ${deadline}.`;
}

function deviceLine(device, locale) {
  const parts = [String(device?.name || UNNAMED_DEVICE)];
  if (device?.address) parts.push(String(device.address));
  if (device?.last_used) {
    const used = new Date(Number(device.last_used) * MILLISECONDS_PER_SECOND);
    parts.push(`last used ${used.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' })}`);
  } else {
    parts.push('never used');
  }
  return parts.join(' · ');
}

function qrModules(makeQr, text) {
  const content = String(text ?? '');
  if (!content) throw new Error("There's nothing to put in the QR code.");
  const code = makeQr(QR_PICK_SIZE_TO_FIT, QR_ERROR_CORRECTION);
  code.addData(content, 'Byte');
  code.make();
  const count = code.getModuleCount();
  return Array.from({ length: count }, (_, row) => Array.from({ length: count }, (_, column) => code.isDark(row, column)));
}

function qrPath(modules) {
  const squares = [];
  modules.forEach((row, y) => row.forEach((dark, x) => {
    if (dark) squares.push(`M${x + QR_BORDER} ${y + QR_BORDER}h1v1h-1z`);
  }));
  return { size: modules.length + 2 * QR_BORDER, path: squares.join('') };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { QR_BORDER, normalisedCode, codesMatch, pendingSummary, deviceLine, qrModules, qrPath };
}
