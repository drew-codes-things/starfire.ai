const http = require('node:http');
const { createHash, createHmac, randomBytes, timingSafeEqual } = require('node:crypto');
const spake2 = require('./spake2');

const LOOPBACK_HOST = '127.0.0.1';
const TOKEN_HEADER = 'X-Arty-Link';
const APP_NAME = 'starfire Desktop';
const STATUS_TIMEOUT_MS = 3000;
const PAIR_TIMEOUT_MS = 8000;
const STEP_TIMEOUT_MS = 200000;
const MAX_RESPONSE_BYTES = 1000000;
const MAX_PICTURE_RESPONSE_BYTES = 8000000;
const MAX_PICTURE_BASE64_CHARS = 6000000;
const MAX_APP_CHARS = 80;
const MAX_SHOWN_CHARS = 300;
const PICTURE_SIGNATURES = { 'image/jpeg': [0xff, 0xd8, 0xff], 'image/png': [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] };
const BASE64_FORMAT = /^[A-Za-z0-9+/]+={0,2}$/;
const HIDDEN_CHARACTER = /[\p{C}\p{Z}]/u;
const LOOK_REASONS = new Set(['declined', 'timed_out', 'not_found', 'cannot_see', 'off_limits', 'unavailable', 'busy', 'bad_request', 'ambiguous', 'changed', 'failed']);
const OPEN_REASONS = new Set(['declined', 'timed_out', 'abandoned', 'busy', 'bad_request', 'no_browser', 'which_browser', 'not_connected', 'unavailable', 'failed']);
const CAPABILITY_IDS = new Set(['screen.look', 'browser.open', 'browser.own', 'screen.control', 'quiz']);
const BROWSER_CAPABILITY = 'browser.open';
const MAX_CAPABILITIES = 10;
const MAX_BROWSERS = 10;
const BROWSER_NAME_FORMAT = /^(Chrome|Edge|Brave|Opera|Vivaldi|Chromium|Firefox)( [2-9])?$/;
const BROWSER_STATES = new Set(['connected']);
const MAX_BROWSER_CHARS = 40;
const MAX_URL_CHARS = 2000;
const WEB_ADDRESS_START = /^https?:\/\//;
const ARTY_VERSION_FORMAT = /^\d+\.\d+\.\d+$/;
const MAX_ARTY_VERSION_CHARS = 20;
const PAIRING_CODE_FORMAT = /^[0-9]{6}$/;
const SESSION_FORMAT = /^[0-9a-f]{32}$/;
const PROOF_FORMAT = /^[0-9a-f]{64}$/;
const PROOF_CONTEXT = 'arty-link-proof';
const NONCE_BYTES = 16;
const TRY_STEP = Object.freeze({ tool: 'computer_apps', arguments: Object.freeze({}) });
const NOT_PROVEN = 'Arty Desktop did not prove it is the app you paired with, so nothing was sent.';
const NOT_ARTY_WHILE_PAIRING = 'Whatever answered did not prove it is Arty Desktop holding that code, so nothing was sent or saved. Check the 6 digits and try again.';
const NOT_ACCEPTED_WHILE_PAIRING = 'The pairing was not accepted. Check that the code is still showing in Arty Desktop and type it again, or make a new code there.';
const NOT_ARTY_WHILE_LINKING = 'Whatever answered did not prove it is Arty Desktop holding this PC\'s link offer, so nothing was sent or saved.';
const NOT_ACCEPTED_WHILE_LINKING = 'Arty Desktop did not accept linking by itself. It makes a new offer after too many tries.';
const MIN_AUTO_LINK_GAP_MS = 30000;
const OFFER_FORMAT = /^[0-9a-f]{32}$/;
const CODE_PAIRING = Object.freeze({ local: false, notArty: NOT_ARTY_WHILE_PAIRING, notAccepted: NOT_ACCEPTED_WHILE_PAIRING });
const SAME_USER_PAIRING = Object.freeze({ local: true, notArty: NOT_ARTY_WHILE_LINKING, notAccepted: NOT_ACCEPTED_WHILE_LINKING });
const NETWORK_FAILURES = ['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'EPIPE'];
const OWN_FAILURES = ['EREDIRECT', 'ETOOLARGE', 'EBADBODY', 'ETIMEDOUT'];
const NOT_RUNNING = 'Arty Desktop is not running on this computer, or it has not finished starting.';
const UNEXPECTED_FAILURE = 'Something went wrong while talking to Arty Desktop, so nothing was done.';

function requestFailure(message, code) {
  return Object.assign(new Error(message), { code });
}

function requestArty({ method, path: urlPath, token, body, port, timeoutMs, maxBytes = MAX_RESPONSE_BYTES }) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body);
    const headers = { Accept: 'application/json' };
    if (token) headers[TOKEN_HEADER] = token;
    if (payload !== null) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(payload);
    }
    const request = http.request({ host: LOOPBACK_HOST, port, method, path: urlPath, headers, timeout: timeoutMs }, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400) {
        request.destroy(requestFailure('Arty Desktop answered with a redirect, which is refused.', 'EREDIRECT'));
        return;
      }
      const chunks = [];
      let received = 0;
      response.on('data', (chunk) => {
        received += chunk.length;
        if (received > maxBytes) {
          request.destroy(requestFailure('Arty Desktop sent an answer that is too large.', 'ETOOLARGE'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => {
        try {
          resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) });
        } catch (error) {
          reject(requestFailure('Did not understand what Arty Desktop answered.', 'EBADBODY'));
        }
      });
      response.on('error', reject);
    });
    request.on('timeout', () => request.destroy(requestFailure('Arty Desktop did not answer in time.', 'ETIMEDOUT')));
    request.on('error', reject);
    request.end(payload);
  });
}

function describeFailure(error) {
  if (NETWORK_FAILURES.includes(error.code)) return NOT_RUNNING;
  if (OWN_FAILURES.includes(error.code)) return error.message;
  return UNEXPECTED_FAILURE;
}

function refuse(error, extra = {}) {
  return { ok: false, error, ...extra };
}

function bodyOf(answer) {
  return answer.body !== null && typeof answer.body === 'object' ? answer.body : {};
}

function isPlainAppName(app) {
  return typeof app === 'string' && app.length <= MAX_APP_CHARS && ![...app].some((character) => character !== ' ' && HIDDEN_CHARACTER.test(character));
}

function shownText(value) {
  return typeof value === 'string' ? [...value].filter((character) => character === ' ' || !HIDDEN_CHARACTER.test(character)).join('').slice(0, MAX_SHOWN_CHARS) : '';
}

function isPicture(body) {
  const signature = PICTURE_SIGNATURES[body.mime];
  if (!signature || typeof body.image !== 'string' || body.image === '' || body.image.length > MAX_PICTURE_BASE64_CHARS || !BASE64_FORMAT.test(body.image)) return false;
  const start = Buffer.from(body.image.slice(0, 16), 'base64');
  return signature.every((byte, index) => start[index] === byte);
}

function isSameProof(given, expected) {
  return typeof given === 'string' && PROOF_FORMAT.test(given) && timingSafeEqual(Buffer.from(given, 'ascii'), Buffer.from(expected, 'ascii'));
}

function expectedProof(token, port, nonce) {
  const fingerprint = createHash('sha256').update(token, 'utf8').digest('hex');
  return createHmac('sha256', fingerprint).update(`${PROOF_CONTEXT}:${port}:${nonce}`).digest('hex');
}

function isPairingAnswer(body) {
  return SESSION_FORMAT.test(String(body.session)) && typeof body.share === 'string' && typeof body.proof === 'string';
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isBrowserName(name) {
  return typeof name === 'string' && name.length <= MAX_BROWSER_CHARS && BROWSER_NAME_FORMAT.test(name);
}

function isWebAddress(url) {
  if (typeof url !== 'string' || url.length > MAX_URL_CHARS || HIDDEN_CHARACTER.test(url)) return false;
  if (!WEB_ADDRESS_START.test(url)) return false;
  try {
    return new URL(url).hostname !== '';
  } catch (error) {
    return false;
  }
}

function connectedBrowsers(browsers) {
  return browsers
    .filter((browser) => isPlainObject(browser) && isBrowserName(browser.name) && BROWSER_STATES.has(browser.state))
    .map((browser) => ({ name: browser.name, state: browser.state }));
}

function hasValidBrowsers(entry) {
  if (!('browsers' in entry)) return true;
  return entry.id === BROWSER_CAPABILITY && Array.isArray(entry.browsers) && entry.browsers.length <= MAX_BROWSERS;
}

function isCapability(entry) {
  return isPlainObject(entry) && CAPABILITY_IDS.has(entry.id) && Number.isSafeInteger(entry.version) && entry.version > 0
    && typeof entry.available === 'boolean' && hasValidBrowsers(entry);
}

function cleanCapability(entry) {
  const capability = { id: entry.id, version: entry.version, available: entry.available };
  if (entry.id === BROWSER_CAPABILITY) capability.browsers = connectedBrowsers(entry.browsers || []);
  return capability;
}

function validCapabilities(raw) {
  if (!Array.isArray(raw) || raw.length > MAX_CAPABILITIES) return [];
  const seen = new Set();
  const kept = [];
  for (const entry of raw.filter(isCapability)) {
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    kept.push(cleanCapability(entry));
  }
  return kept;
}

function validArtyVersion(raw) {
  return typeof raw === 'string' && raw.length <= MAX_ARTY_VERSION_CHARS && ARTY_VERSION_FORMAT.test(raw) ? raw : '';
}

function linkState(state, paired, linkedByItself = false) {
  return { state, paired, linkedByItself: paired && linkedByItself, capabilities: [], artyDesktop: '' };
}

function connectedState(body, linkedByItself) {
  return { ...linkState('connected', true, linkedByItself), computer: body.computer === true, capabilities: validCapabilities(body.capabilities), artyDesktop: validArtyVersion(body.arty_desktop) };
}

function withLocalMark(body, kind) {
  return kind.local ? { ...body, local: true } : body;
}

function lookFailure(reason, error) {
  return { ok: false, reason, error };
}

function describeLookRefusal(answer) {
  const body = bodyOf(answer);
  const reason = LOOK_REASONS.has(body.reason) ? body.reason : 'failed';
  return lookFailure(reason, typeof body.error === 'string' ? shownText(body.error) : `Arty Desktop answered ${answer.status}.`);
}

function describeOpenRefusal(answer) {
  const body = bodyOf(answer);
  const reason = OPEN_REASONS.has(body.reason) ? body.reason : 'failed';
  return lookFailure(reason, typeof body.error === 'string' ? shownText(body.error) : `Arty Desktop answered ${answer.status}.`);
}

function openedTab(tab) {
  return Number.isSafeInteger(tab) && tab >= 0 ? tab : null;
}

function createArtyLink({ getSettings, setToken, request = requestArty, readOffer = () => null, now = Date.now }) {
  let lastAutoLinkAt = -Infinity;
  async function isProvenArty(settings) {
    const nonce = randomBytes(NONCE_BYTES).toString('hex');
    const answer = await request({ method: 'GET', path: `/link/proof?nonce=${nonce}`, port: settings.artyPort, timeoutMs: STATUS_TIMEOUT_MS });
    return answer.status === 200 && isSameProof(bodyOf(answer).proof, expectedProof(settings.artyToken, settings.artyPort, nonce));
  }

  async function status() {
    const settings = getSettings();
    const paired = settings.artyToken !== '';
    const byItself = settings.artyLinkedByItself === true;
    if (!settings.artyLink) return linkState('off', paired, byItself);
    if (!paired) return linkState('unpaired', false);
    let answer;
    try {
      if (!(await isProvenArty(settings))) return linkState('unproven', true, byItself);
      answer = await request({ method: 'GET', path: '/link/status', token: settings.artyToken, port: settings.artyPort, timeoutMs: STATUS_TIMEOUT_MS });
    } catch (error) {
      return linkState('unreachable', true, byItself);
    }
    if (answer.status === 403) return linkState('unproven', true, byItself);
    if (answer.status !== 200 || bodyOf(answer).ok !== true) return linkState('unreachable', true, byItself);
    return connectedState(bodyOf(answer), byItself);
  }

  async function finishPairing(settings, password, kind, secret, share, started) {
    let key;
    try {
      key = spake2.sharedKey(secret, password, spake2.APP, share, started.share);
    } catch (error) {
      return refuse(kind.notArty);
    }
    if (!isSameProof(started.proof, spake2.proof(key, spake2.ARTY))) return refuse(kind.notArty);
    const finish = withLocalMark({ session: started.session, proof: spake2.proof(key, spake2.APP) }, kind);
    const answer = await request({ method: 'POST', path: '/link/pair/finish', body: finish, port: settings.artyPort, timeoutMs: PAIR_TIMEOUT_MS });
    const body = bodyOf(answer);
    if (answer.status !== 200 || body.ok !== true) return refuse(typeof body.error === 'string' ? shownText(body.error) : 'Arty Desktop did not finish the pairing.');
    const saved = setToken(spake2.linkToken(key), { byItself: kind.local });
    return saved.ok ? { ok: true } : saved;
  }

  async function pairWithPassword(settings, password, kind) {
    const secret = spake2.newSecret();
    const share = spake2.shareHex(secret, password, spake2.APP);
    try {
      const start = withLocalMark({ name: APP_NAME, share }, kind);
      const answer = await request({ method: 'POST', path: '/link/pair/start', body: start, port: settings.artyPort, timeoutMs: PAIR_TIMEOUT_MS });
      const body = bodyOf(answer);
      if (answer.status !== 200) return refuse(kind.notAccepted);
      if (!isPairingAnswer(body)) return refuse(kind.notArty);
      return await finishPairing(settings, password, kind, secret, share, body);
    } catch (error) {
      return refuse(describeFailure(error));
    }
  }

  async function pair(rawCode) {
    const settings = getSettings();
    if (!settings.artyLink) return refuse('Switch the link to Arty Desktop on first.');
    const code = typeof rawCode === 'string' ? rawCode.replace(/\s+/g, '') : '';
    if (!PAIRING_CODE_FORMAT.test(code)) return refuse('The code from Arty Desktop is 6 digits.');
    return pairWithPassword(settings, code, CODE_PAIRING);
  }

  function mayLinkByItself(settings) {
    return settings.artyAutoLink === true && settings.artyLinkTurnedOff !== true && settings.artyToken === '';
  }

  async function linkByItself() {
    const settings = getSettings();
    if (!mayLinkByItself(settings)) return refuse('Linking by itself is off, or a link already exists.', { skipped: true });
    if (now() - lastAutoLinkAt < MIN_AUTO_LINK_GAP_MS) return refuse('Linking by itself was tried less than 30 seconds ago.', { skipped: true });
    const offer = readOffer();
    if (typeof offer !== 'string' || !OFFER_FORMAT.test(offer)) return refuse('Arty Desktop has not offered to link by itself.', { skipped: true });
    lastAutoLinkAt = now();
    return pairWithPassword(settings, offer, SAME_USER_PAIRING);
  }

  async function unpair() {
    const settings = getSettings();
    if (settings.artyToken === '') return setToken('');
    try {
      if (await isProvenArty(settings)) {
        await request({ method: 'POST', path: '/link/unlink', token: settings.artyToken, body: {}, port: settings.artyPort, timeoutMs: PAIR_TIMEOUT_MS });
      }
    } catch (error) {
      return setToken('');
    }
    return setToken('');
  }

  async function tryLink() {
    const settings = getSettings();
    if (!settings.artyLink) return refuse('The link to Arty Desktop is off.');
    if (settings.artyToken === '') return refuse('Pair with Arty Desktop first.');
    let answer;
    try {
      if (!(await isProvenArty(settings))) return refuse(NOT_PROVEN);
      answer = await request({ method: 'POST', path: '/link/computer', token: settings.artyToken, body: TRY_STEP, port: settings.artyPort, timeoutMs: STEP_TIMEOUT_MS });
    } catch (error) {
      return refuse(describeFailure(error));
    }
    const body = bodyOf(answer);
    if (answer.status === 200 && body.ok === true) return { ok: true, result: String(body.result) };
    const message = typeof body.error === 'string' ? shownText(body.error) : `Arty Desktop answered ${answer.status}.`;
    return body.declined === true ? refuse(message, { declined: true }) : refuse(message);
  }

  async function lookAtScreen(app) {
    const settings = getSettings();
    if (!settings.artyLink) return lookFailure('not_linked', 'The link to Arty Desktop is off.');
    if (settings.artyToken === '') return lookFailure('not_linked', 'Pair with Arty Desktop first.');
    if (!isPlainAppName(app)) return lookFailure('bad_request', 'That is not an app name that can be shown on the Allow card.');
    let answer;
    try {
      if (!(await isProvenArty(settings))) return lookFailure('not_linked', NOT_PROVEN);
      answer = await request({ method: 'POST', path: '/link/screenshot', token: settings.artyToken, body: { app }, port: settings.artyPort, timeoutMs: STEP_TIMEOUT_MS, maxBytes: MAX_PICTURE_RESPONSE_BYTES });
    } catch (error) {
      return lookFailure(NETWORK_FAILURES.includes(error.code) ? 'not_running' : 'failed', describeFailure(error));
    }
    const body = bodyOf(answer);
    if (answer.status !== 200 || body.ok !== true) return describeLookRefusal(answer);
    if (!isPicture(body)) return lookFailure('failed', 'Arty Desktop sent something that is not a picture, so it was not used.');
    return { ok: true, image: body.image, mime: body.mime, target: shownText(body.target), window: shownText(body.window), note: shownText(body.note) };
  }

  async function openInBrowser(url, browser) {
    const settings = getSettings();
    if (!settings.artyLink) return lookFailure('not_linked', 'The link to Arty Desktop is off.');
    if (settings.artyToken === '') return lookFailure('not_linked', 'Pair with Arty Desktop first.');
    if (!isWebAddress(url)) return lookFailure('bad_request', 'That is not a web address that can be shown plainly on the Allow card.');
    if (browser !== '' && !isBrowserName(browser)) return lookFailure('bad_request', 'That is not the name of a browser Arty Desktop knows.');
    let answer;
    try {
      if (!(await isProvenArty(settings))) return lookFailure('not_linked', NOT_PROVEN);
      answer = await request({ method: 'POST', path: '/link/browser', token: settings.artyToken, body: { action: 'open', url, browser }, port: settings.artyPort, timeoutMs: STEP_TIMEOUT_MS });
    } catch (error) {
      return lookFailure(NETWORK_FAILURES.includes(error.code) ? 'not_running' : 'failed', describeFailure(error));
    }
    const body = bodyOf(answer);
    if (answer.status !== 200 || body.ok !== true) return describeOpenRefusal(answer);
    return { ok: true, browser: isBrowserName(body.browser) ? body.browser : '', tab: openedTab(body.tab) };
  }

  return { status, pair, linkByItself, unpair, tryLink, lookAtScreen, openInBrowser };
}

module.exports = { createArtyLink, requestArty, LOOPBACK_HOST };
