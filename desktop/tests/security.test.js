const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { LOCAL_PAGE_CSP } = require('../src/csp');
const { secureWebPreferences, LOCAL_PARTITION, STARFIRE_PARTITION } = require('../src/webPreferences');

const ROOT = path.join(__dirname, '..');
const PAGES = path.join(ROOT, 'pages');
const pageFiles = fs.readdirSync(PAGES).filter((name) => name.endsWith('.html'));

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' || entry.name === 'release' ? [] : sourceFiles(full);
    return /\.(js|html|css|json)$/.test(entry.name) && entry.name !== 'package-lock.json' ? [full] : [];
  });
}

test('there are local pages to check', () => {
  assert.ok(pageFiles.length >= 3, pageFiles.join(','));
});

test('the CSP is strict: nothing remote, no inline script, no eval', () => {
  assert.match(LOCAL_PAGE_CSP, /default-src 'none'/);
  assert.match(LOCAL_PAGE_CSP, /script-src 'self'(;|$)/);
  assert.doesNotMatch(LOCAL_PAGE_CSP, /unsafe-inline|unsafe-eval|\*|https?:/);
  assert.match(LOCAL_PAGE_CSP, /connect-src 'none'/);
  assert.match(LOCAL_PAGE_CSP, /base-uri 'none'/);
  assert.match(LOCAL_PAGE_CSP, /form-action 'none'/);
  assert.match(LOCAL_PAGE_CSP, /object-src 'none'/);
});

test('every local page carries exactly that CSP and nothing inline', () => {
  for (const name of pageFiles) {
    const html = fs.readFileSync(path.join(PAGES, name), 'utf8');
    const metas = [...html.matchAll(/<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"/g)];
    assert.equal(metas.length, 1, `${name}: one CSP meta`);
    assert.equal(metas[0][1], LOCAL_PAGE_CSP, `${name}: CSP text`);
    assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/i, `${name}: inline script`);
    assert.doesNotMatch(html, /<style[\s>]/i, `${name}: inline style block`);
    assert.doesNotMatch(html, /\sstyle\s*=/i, `${name}: style attribute`);
    assert.doesNotMatch(html, /\son[a-z]+\s*=/i, `${name}: event handler attribute`);
    assert.doesNotMatch(html, /(src|href)\s*=\s*"(https?:)?\/\//i, `${name}: remote resource`);
  }
});

test('window preferences keep the renderer isolated', () => {
  const preferences = secureWebPreferences({ partition: STARFIRE_PARTITION });
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.webSecurity, true);
  assert.equal(preferences.allowRunningInsecureContent, false);
  assert.equal(preferences.webviewTag, false);
  assert.equal(preferences.nodeIntegrationInSubFrames, false);
  assert.equal(preferences.experimentalFeatures, false);
  assert.equal(preferences.partition, 'persist:starfire');
});

test('callers cannot weaken the isolation through the extra options', () => {
  const preferences = secureWebPreferences({ contextIsolation: false, nodeIntegration: true, sandbox: false, webSecurity: false, preload: '/x/preload.js' });
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.webSecurity, true);
  assert.equal(preferences.preload, '/x/preload.js');
});

test('the signed-in session persists and the local pages never share it', () => {
  assert.match(STARFIRE_PARTITION, /^persist:/);
  assert.notEqual(LOCAL_PARTITION, STARFIRE_PARTITION);
});

test('the app uses no remote module, no webview and no node integration anywhere', () => {
  const banned = [/enableRemoteModule/, /@electron\/remote/, /require\(['"]electron['"]\)\.remote/, /nodeIntegration:\s*true/, /contextIsolation:\s*false/, /sandbox:\s*false/, /webviewTag:\s*true/, /webSecurity:\s*false/, /allowRunningInsecureContent:\s*true/, /--no-sandbox/, /disable-web-security/, /ignore-certificate-errors/, /certificate-error/];
  for (const file of sourceFiles(ROOT)) {
    if (file.endsWith('security.test.js')) continue;
    const text = fs.readFileSync(file, 'utf8');
    for (const pattern of banned) {
      assert.doesNotMatch(text, pattern, `${path.relative(ROOT, file)} matches ${pattern}`);
    }
  }
});

test('no source file contains an em dash', () => {
  for (const file of sourceFiles(ROOT)) {
    assert.equal(fs.readFileSync(file, 'utf8').includes(String.fromCharCode(0x2014)), false, path.relative(ROOT, file));
  }
});

test('the settings page hands plain key fields to the bridge, never the event itself', () => {
  const page = fs.readFileSync(path.join(PAGES, 'settings.js'), 'utf8');
  assert.match(page, /describeKey\(\{\s*code: event\.code/);
});

test('the preload exposes only channels that the main process handles', () => {
  const preload = fs.readFileSync(path.join(ROOT, 'preload.js'), 'utf8');
  const ipc = fs.readFileSync(path.join(ROOT, 'src', 'ipc.js'), 'utf8');
  const used = [...preload.matchAll(/'(starfire:[a-z-]+)'/g)].map((match) => match[1]);
  const handled = new Set([...ipc.matchAll(/'(starfire:[a-z-]+)'/g)].map((match) => match[1]));
  assert.ok(used.length >= 6, used.join(','));
  for (const channel of used) {
    assert.ok(handled.has(channel), `${channel} has no handler`);
  }
});

test('the packaged files list covers every module the app requires', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  assert.ok(pkg.build.files.includes('src/**/*'));
  assert.ok(pkg.build.files.includes('pages/**/*'));
  assert.ok(pkg.build.files.includes('main.js'));
  assert.ok(pkg.build.files.includes('preload.js'));
});
