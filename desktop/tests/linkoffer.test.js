const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { offerFile, readOffer } = require('../src/linkOffer');

const SECRET = '0123456789abcdef0123456789abcdef';
const NO_POSIX_OWNERS = process.platform === 'win32' && 'Windows has no Unix owners or permission bits';

function scratchFolder() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'sfd-offer-'));
}

function writeOffer(contents, mode = 0o600) {
  const folder = scratchFolder();
  const file = path.join(folder, '.arty_link_offer');
  fs.writeFileSync(file, contents, { mode });
  fs.chmodSync(file, mode);
  return file;
}

test('the offer file sits in Arty\'s own data folder beside starfire\'s', () => {
  assert.equal(offerFile('/home/drew/.config'), path.join('/home/drew/.config', 'Arty', '.arty_link_offer'));
  assert.equal(offerFile(''), '');
  assert.equal(offerFile(5), '');
});

test('a private offer file of the user with 32 lowercase hex characters is read', { skip: NO_POSIX_OWNERS }, () => {
  assert.equal(readOffer(writeOffer(SECRET)), SECRET);
});

test('a missing file or no path gives no offer', () => {
  assert.equal(readOffer(path.join(scratchFolder(), '.arty_link_offer')), null);
  assert.equal(readOffer(''), null);
  assert.equal(readOffer(undefined), null);
});

test('a file someone else owns is ignored', { skip: NO_POSIX_OWNERS }, () => {
  const file = writeOffer(SECRET);
  assert.equal(readOffer(file, { userId: process.getuid() + 1 }), null);
  assert.equal(readOffer(file, { userId: process.getuid() }), SECRET);
});

test('a file the group or others can read or write is ignored', { skip: NO_POSIX_OWNERS }, () => {
  for (const mode of [0o640, 0o604, 0o620, 0o602, 0o660, 0o644, 0o666, 0o610]) {
    assert.equal(readOffer(writeOffer(SECRET, mode)), null, mode.toString(8));
  }
  assert.equal(readOffer(writeOffer(SECRET, 0o400)), SECRET);
});

test('a symlink is ignored even when it points at a good offer', { skip: NO_POSIX_OWNERS }, () => {
  const target = writeOffer(SECRET);
  const link = path.join(scratchFolder(), '.arty_link_offer');
  fs.symlinkSync(target, link);
  assert.equal(readOffer(link), null);
});

test('a folder in place of the file is ignored', { skip: NO_POSIX_OWNERS }, () => {
  const folder = path.join(scratchFolder(), '.arty_link_offer');
  fs.mkdirSync(folder, { mode: 0o700 });
  assert.equal(readOffer(folder), null);
});

test('contents that are not exactly 32 lowercase hex characters are ignored', { skip: NO_POSIX_OWNERS }, () => {
  const bad = [
    '', `${SECRET}\n`, ` ${SECRET}`, SECRET.toUpperCase(), SECRET.slice(1), `${SECRET}0`, `${SECRET.slice(0, 31)}g`,
    `${SECRET.slice(0, 31)}é`, Buffer.from([...Buffer.from(SECRET.slice(0, 31)), 0xe6]),
  ];
  for (const contents of bad) assert.equal(readOffer(writeOffer(contents)), null, JSON.stringify(String(contents)));
});

test('an oversized file is ignored without reading it all', { skip: NO_POSIX_OWNERS }, () => {
  assert.equal(readOffer(writeOffer(`${SECRET}${' '.repeat(33)}`)), null);
  assert.equal(readOffer(writeOffer(SECRET.repeat(1000))), null);
});
