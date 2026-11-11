'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'artyDevicesLogic.js'));
const { safeNextPath } = require(path.join(__dirname, '..', '..', 'static', 'loginNext.js'));
const makeQr = require(path.join(__dirname, '..', '..', 'static', 'vendor', 'qrcode-generator.js'));

test('a code is read in either case, with or without the dash, and nothing else is', () => {
  assert.equal(logic.normalisedCode('abcd-efgh'), 'ABCD-EFGH');
  assert.equal(logic.normalisedCode(' ABCDEFGH '), 'ABCD-EFGH');
  assert.equal(logic.normalisedCode('ABCD-EFG1'), null);
  assert.equal(logic.normalisedCode('<img src=x>'), null);
  assert.equal(logic.normalisedCode(''), null);
});

test('approving needs the code typed from the terminal', () => {
  assert.equal(logic.codesMatch('ABCD-EFGH', 'abcd efgh'), true);
  assert.equal(logic.codesMatch('ABCD-EFGH', 'ABCD-EFGJ'), false);
  assert.equal(logic.codesMatch('ABCD-EFGH', ''), false);
});

test('a waiting computer is described as its own claim with where it asked from', () => {
  assert.equal(logic.pendingSummary({ name: 'Arty CLI on box', address: '192.0.2.5', expires_in: 125 }),
    '“Arty CLI on box” asked from 192.0.2.5. The code runs out in 2 minutes.');
  assert.equal(logic.pendingSummary({}), '“A device” is waiting. The code runs out soon.');
});

test('an approved device line shows its name, address and when it was last used', () => {
  const day = Date.UTC(2026, 10, 11, 12, 0, 0) / 1000;
  assert.equal(logic.deviceLine({ name: 'laptop', address: '192.0.2.5', created: day, last_used: null }, 'en-GB'),
    'laptop · 192.0.2.5 · never used');
  assert.match(logic.deviceLine({ name: 'phone', address: '', created: day, last_used: day }, 'en-GB'), /^phone · last used /);
});

test('a QR drawing has one square per dark module inside a quiet border', () => {
  const modules = logic.qrModules(makeQr, '{"arty":1,"url":"https://arty.example","key":"arty_dev_x"}');
  const drawn = logic.qrPath(modules);
  assert.equal(drawn.size, modules.length + 2 * logic.QR_BORDER);
  assert.equal(drawn.path.match(/h1v1h-1z/g).length, modules.flat().filter(Boolean).length);
  assert.throws(() => logic.qrModules(makeQr, ''), /nothing to put in the QR code/);
});

test('after logging in only a page on this site is opened', () => {
  assert.equal(safeNextPath('?next=%2Farty-devices.html%3Fcode%3DABCD-EFGH'), '/arty-devices.html?code=ABCD-EFGH');
  assert.equal(safeNextPath(''), '/');
  assert.equal(safeNextPath('?next=https%3A%2F%2Fevil.example'), '/');
  assert.equal(safeNextPath('?next=%2F%2Fevil.example'), '/');
  assert.equal(safeNextPath('?next=%2F%5Cevil.example'), '/');
  assert.equal(safeNextPath('?next=javascript%3Aalert(1)'), '/');
  assert.equal(safeNextPath('?next=%2Fok%0Abad'), '/');
});
