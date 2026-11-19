const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
const NOREPLY_ADDRESS = /^\d+\+[A-Za-z0-9-]+@users\.noreply\.github\.com$/;

function linuxTargetNames() {
  return pkg.build.linux.target.map((entry) => (typeof entry === 'string' ? entry : entry.target));
}

test('the Linux build produces a Debian package next to the AppImage', () => {
  assert.ok(linuxTargetNames().includes('deb'), linuxTargetNames().join(','));
  assert.ok(linuxTargetNames().includes('AppImage'));
});

test('the Debian package carries the app name, a menu category and a description', () => {
  assert.equal(pkg.build.deb.packageName, 'starfire');
  assert.equal(pkg.build.deb.packageCategory, 'net');
  assert.equal(pkg.build.productName, 'starfire');
  assert.ok(pkg.build.linux.category);
  assert.ok(pkg.build.linux.synopsis);
  assert.ok(pkg.description.length > 20);
  assert.ok(fs.existsSync(path.join(__dirname, '..', pkg.build.linux.icon)));
});

test('the package author is a no-reply address, not a personal one', () => {
  assert.match(pkg.author.email, NOREPLY_ADDRESS);
  const addresses = JSON.stringify(pkg).match(/[\w.+-]+@[\w.-]+/g) || [];
  assert.deepEqual([...new Set(addresses)], [pkg.author.email]);
});

test('there is an npm script for the Debian package', () => {
  assert.match(pkg.scripts.deb, /electron-builder .*--linux deb/);
});
