const http = require('node:http');
const { isArtyToken } = require('./settings');

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
const LOOK_REASONS = new Set(['declined', 'timed_out', 'not_found', 'cannot_see', 'off_limits', 'unavailable', 'busy', 'bad_request', 'failed']);
const PAIRING_CODE_FORMAT = /^[0-9]{6}$/;
const COMPUTER_TOOL_FORMAT = /^computer_[a-z_]{1,40}$/;
const NETWORK_FAILURES = ['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'EPIPE'];
const NOT_RUNNING = 'Arty Desktop is not running on this computer, or it has not finished starting.';

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
  return error.message;
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

function lookFailure(reason, error) {
  return { ok: false, reason, error };
}

function describeLookRefusal(answer) {
  const body = bodyOf(answer);
  const reason = LOOK_REASONS.has(body.reason) ? body.reason : 'failed';
  return lookFailure(reason, typeof body.error === 'string' ? shownText(body.error) : `Arty Desktop answered ${answer.status}.`);
}

function createArtyLink({ getSettings, setToken, request = requestArty }) {
  async function status() {
    const settings = getSettings();
    const paired = settings.artyToken !== '';
    if (!settings.artyLink) return { state: 'off', paired };
    if (!paired) return { state: 'unpaired', paired: false };
    let answer;
    try {
      answer = await request({ method: 'GET', path: '/link/status', token: settings.artyToken, port: settings.artyPort, timeoutMs: STATUS_TIMEOUT_MS });
    } catch (error) {
      return { state: 'unreachable', paired: true };
    }
    if (answer.status === 403) {
      setToken('');
      return { state: 'unpaired', paired: false };
    }
    if (answer.status !== 200 || bodyOf(answer).ok !== true) return { state: 'unreachable', paired: true };
    return { state: 'connected', paired: true, computer: bodyOf(answer).computer === true };
  }

  async function pair(rawCode) {
    const settings = getSettings();
    if (!settings.artyLink) return refuse('Switch the link to Arty Desktop on first.');
    const code = typeof rawCode === 'string' ? rawCode.replace(/\s+/g, '') : '';
    if (!PAIRING_CODE_FORMAT.test(code)) return refuse('The code from Arty Desktop is 6 digits.');
    let answer;
    try {
      answer = await request({ method: 'POST', path: '/link/pair', body: { code, name: APP_NAME }, port: settings.artyPort, timeoutMs: PAIR_TIMEOUT_MS });
    } catch (error) {
      return refuse(describeFailure(error));
    }
    const body = bodyOf(answer);
    if (answer.status !== 200) return refuse(typeof body.error === 'string' ? body.error : 'Arty Desktop did not accept that code.');
    if (!isArtyToken(body.token)) return refuse('Arty Desktop sent something that is not a token, so nothing was saved.');
    const saved = setToken(body.token);
    return saved.ok ? { ok: true } : saved;
  }

  async function unpair() {
    const settings = getSettings();
    if (settings.artyToken !== '') {
      try {
        await request({ method: 'POST', path: '/link/unlink', token: settings.artyToken, body: {}, port: settings.artyPort, timeoutMs: PAIR_TIMEOUT_MS });
      } catch (error) {
        setToken('');
        return { ok: true };
      }
    }
    return setToken('');
  }

  async function runComputer(tool, args) {
    const settings = getSettings();
    if (!settings.artyLink) return refuse('The link to Arty Desktop is off.');
    if (settings.artyToken === '') return refuse('Pair with Arty Desktop first.');
    const isPlainObject = args !== null && typeof args === 'object' && !Array.isArray(args);
    if (typeof tool !== 'string' || !COMPUTER_TOOL_FORMAT.test(tool) || !isPlainObject) return refuse('That is not a computer step Arty Desktop can take.');
    let answer;
    try {
      answer = await request({ method: 'POST', path: '/link/computer', token: settings.artyToken, body: { tool, arguments: args }, port: settings.artyPort, timeoutMs: STEP_TIMEOUT_MS });
    } catch (error) {
      return refuse(describeFailure(error));
    }
    const body = bodyOf(answer);
    if (answer.status === 200 && body.ok === true) return { ok: true, result: String(body.result) };
    const message = typeof body.error === 'string' ? body.error : `Arty Desktop answered ${answer.status}.`;
    return body.declined === true ? refuse(message, { declined: true }) : refuse(message);
  }

  async function lookAtScreen(app) {
    const settings = getSettings();
    if (!settings.artyLink) return lookFailure('not_linked', 'The link to Arty Desktop is off.');
    if (settings.artyToken === '') return lookFailure('not_linked', 'Pair with Arty Desktop first.');
    if (!isPlainAppName(app)) return lookFailure('bad_request', 'That is not an app name that can be shown on the Allow card.');
    let answer;
    try {
      answer = await request({ method: 'POST', path: '/link/screenshot', token: settings.artyToken, body: { app }, port: settings.artyPort, timeoutMs: STEP_TIMEOUT_MS, maxBytes: MAX_PICTURE_RESPONSE_BYTES });
    } catch (error) {
      return lookFailure(NETWORK_FAILURES.includes(error.code) ? 'not_running' : 'failed', describeFailure(error));
    }
    const body = bodyOf(answer);
    if (answer.status !== 200 || body.ok !== true) return describeLookRefusal(answer);
    if (!isPicture(body)) return lookFailure('failed', 'Arty Desktop sent something that is not a picture, so it was not used.');
    return { ok: true, image: body.image, mime: body.mime, target: shownText(body.target), window: shownText(body.window), note: shownText(body.note) };
  }

  return { status, pair, unpair, runComputer, lookAtScreen };
}

module.exports = { createArtyLink, requestArty, LOOPBACK_HOST };
