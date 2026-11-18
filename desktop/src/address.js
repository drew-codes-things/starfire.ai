const HEALTH_PATH = '/api/health';
const HEALTH_TIMEOUT_MS = 8000;
const HEALTH_MAX_CHARS = 4096;
const VERSION_MAX_CHARS = 64;
const SCHEME_PREFIX = /^[a-z][a-z0-9+.-]*:\/\//i;
const IPV4_HOST = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
const IPV6_LOCAL_PREFIXES = ['fc', 'fd', 'fe8', 'fe9', 'fea', 'feb'];

function isLocalIpv4(hostname) {
  const match = IPV4_HOST.exec(hostname);
  if (!match) return false;
  const [first, second] = [Number(match[1]), Number(match[2])];
  if (first === 127 || first === 10) return true;
  if (first === 172) return second >= 16 && second <= 31;
  if (first === 192) return second === 168;
  return first === 169 && second === 254;
}

function isLocalIpv6(hostname) {
  if (!hostname.startsWith('[') || !hostname.endsWith(']')) return false;
  const bare = hostname.slice(1, -1).toLowerCase();
  if (bare === '::1') return true;
  return IPV6_LOCAL_PREFIXES.some((prefix) => bare.startsWith(prefix));
}

function isLocalNetworkHost(hostname) {
  const host = String(hostname).toLowerCase();
  if (host === 'localhost') return true;
  if (host.endsWith('.local')) return true;
  return isLocalIpv4(host) || isLocalIpv6(host);
}

function refuse(error) {
  return { ok: false, error };
}

function normalizeAddress(input, { allowInsecure = false } = {}) {
  if (typeof input !== 'string' || input.trim() === '') {
    return refuse('Enter the address of your starfire.');
  }
  const trimmed = input.trim();
  const withScheme = SCHEME_PREFIX.test(trimmed) ? trimmed : `https://${trimmed}`;
  let url;
  try {
    url = new URL(withScheme);
  } catch (error) {
    return refuse('That does not look like an address.');
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    return refuse('Use an https address.');
  }
  if (url.username || url.password) {
    return refuse('Leave the user name and password out of the address.');
  }
  if (!url.hostname) {
    return refuse('That does not look like an address.');
  }
  if (url.protocol === 'http:') {
    if (!isLocalNetworkHost(url.hostname)) {
      return refuse('Only https is allowed outside your own network. Use an https address.');
    }
    if (!allowInsecure) {
      return refuse('http is only for localhost and your own network. Tick the box to allow it.');
    }
  }
  return { ok: true, origin: url.origin };
}

function describeFailure(error, host) {
  if (error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
    return `${host} did not answer in time.`;
  }
  return `Could not reach ${host}. Check the address and your connection.`;
}

function parseHealth(text) {
  if (text.length > HEALTH_MAX_CHARS) return null;
  let body;
  try {
    body = JSON.parse(text);
  } catch (error) {
    return null;
  }
  const isHealthy = body !== null && typeof body === 'object' && body.ok === true;
  const version = isHealthy ? body.version : null;
  const hasVersion = typeof version === 'string' && version.length > 0 && version.length <= VERSION_MAX_CHARS;
  return hasVersion ? { ok: true, version } : null;
}

async function checkStarfire(origin, { fetchImpl = fetch, timeoutMs = HEALTH_TIMEOUT_MS } = {}) {
  const host = new URL(origin).host;
  const notStarfire = refuse(`${host} answered, but it does not look like starfire.`);
  let response;
  try {
    response = await fetchImpl(`${origin}${HEALTH_PATH}`, {
      redirect: 'manual',
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    return refuse(describeFailure(error, host));
  }
  if (!response.ok || response.type === 'opaqueredirect') return notStarfire;
  let text;
  try {
    text = await response.text();
  } catch (error) {
    return refuse(describeFailure(error, host));
  }
  return parseHealth(text) || notStarfire;
}

module.exports = { normalizeAddress, checkStarfire, isLocalNetworkHost, HEALTH_PATH };
