'use strict';

const appKeyEls = {
  form: document.getElementById('appKeyForm'), name: document.getElementById('appKeyName'),
  account: document.getElementById('appKeyAccount'), ownToolsRow: document.getElementById('appKeyOwnToolsRow'),
  ownTools: document.getElementById('appKeyOwnTools'),
  models: document.getElementById('appKeyModels'), dailyLimit: document.getElementById('appKeyDailyLimit'),
  expiryDays: document.getElementById('appKeyExpiryDays'), submit: document.getElementById('createAppKeyBtn'),
  result: document.getElementById('appKeyResult'), shown: document.getElementById('appKeyShown'),
  secret: document.getElementById('appKeySecret'), copy: document.getElementById('copyAppKeyBtn'),
  list: document.getElementById('appKeyList'),
};

function appKeyElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showAppKeyNote(text, kind) {
  appKeyEls.result.textContent = text;
  appKeyEls.result.className = 'settings-hint' + (kind ? ' ' + kind : '');
}

async function appKeyRequest(method, url, body) {
  const r = await fetch(url, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await failureMessage(r, `failed (${r.status})`));
  return r.json();
}

function appKeyWhen(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString() : 'never';
}

function appKeyGrantText(grant) {
  const connection = grant.connection || 'removed connection';
  return grant.model === '*' ? `every model of ${connection}` : `${grant.model} on ${connection}`;
}

function appKeyMeta(key) {
  const parts = [key.account || 'account removed', `made ${appKeyWhen(key.created)}`, `last used ${appKeyWhen(key.last_used)}`,
    key.grants.map(appKeyGrantText).join(', ')];
  if (key.allow_own_tools) parts.push('may use Arty');
  if (key.daily_limit) parts.push(`${key.daily_limit} replies a day`);
  if (key.expires) parts.push(`expires ${appKeyWhen(key.expires)}`);
  if (!key.account_open) parts.push('account closed, key refused');
  return parts.join(' · ');
}

function appKeyRow(key) {
  const li = appKeyElement('li', 'endpoint-row');
  const label = appKeyElement('span', 'endpoint-label', `${key.name} `);
  label.append(appKeyElement('span', 'endpoint-meta', `${key.shown_start}… · ${appKeyMeta(key)}`));
  const revoke = appKeyElement('button', 'btn ghost', 'revoke');
  revoke.type = 'button';
  revoke.setAttribute('aria-label', `Revoke the app key ${key.name}`);
  revoke.onclick = async () => {
    if (!await confirmRemove(`the app key ${key.name}: apps using it stop at once`)) return;
    try {
      await appKeyRequest('DELETE', `/api/app-keys/${encodeURIComponent(key.id)}`);
      showAppKeyNote(`revoked ${key.name}`, 'ok');
    } catch (e) {
      showAppKeyNote(e.message, 'error');
    }
    refreshAppKeys();
  };
  li.append(label, revoke);
  return li;
}

async function refreshAppKeyAccounts() {
  const chosen = appKeyEls.account.value;
  const { users = [] } = await appKeyRequest('GET', '/api/auth/users');
  appKeyEls.account.replaceChildren(...users.filter(user => !user.disabled).map(user => {
    const option = appKeyElement('option', '', `${user.name} (${user.email}, ${user.role})`);
    option.value = user.id;
    return option;
  }));
  if (chosen && [...appKeyEls.account.options].some(option => option.value === chosen)) appKeyEls.account.value = chosen;
}

let appKeyChoices = { admin: false, connections: [] };

function appKeyCheckbox(text, value, dataset) {
  const label = appKeyElement('label', 'theme-toggle');
  const box = appKeyElement('input');
  box.type = 'checkbox';
  box.value = value;
  Object.assign(box.dataset, dataset);
  label.append(box, appKeyElement('span', '', text));
  const row = appKeyElement('div', 'settings-row');
  row.append(label);
  return row;
}

function appKeyConnectionBlock(connection, showOwnTools) {
  const block = appKeyElement('div', 'app-key-connection');
  const state = connection.available ? 'available' : 'unavailable';
  block.append(appKeyCheckbox(`Every model of ${connection.label} (${state})`, connection.endpoint_id,
    { endpoint: connection.endpoint_id, every: 'yes' }));
  for (const model of connection.models) {
    if (model.own_tools && !showOwnTools) continue;
    block.append(appKeyCheckbox(model.own_tools ? `${model.name} (Arty, acts as you)` : model.name, model.name,
      { endpoint: connection.endpoint_id }));
  }
  return block;
}

function renderAppKeyModels() {
  appKeyEls.ownToolsRow.hidden = !appKeyChoices.admin;
  if (!appKeyChoices.admin) appKeyEls.ownTools.checked = false;
  const blocks = appKeyChoices.connections.map(c => appKeyConnectionBlock(c, appKeyEls.ownTools.checked));
  appKeyEls.models.replaceChildren(...blocks);
  if (!blocks.length) appKeyEls.models.append(appKeyElement('p', 'settings-hint', 'no connections yet'));
}

async function refreshAppKeyModels() {
  if (!appKeyEls.account.value) return;
  appKeyEls.models.replaceChildren(appKeyElement('p', 'settings-hint', 'loading models…'));
  try {
    appKeyChoices = await appKeyRequest('GET', `/api/app-keys/models/${encodeURIComponent(appKeyEls.account.value)}`);
  } catch (e) {
    appKeyChoices = { admin: false, connections: [] };
    appKeyEls.models.replaceChildren(appKeyElement('p', 'settings-hint error', e.message));
    return;
  }
  renderAppKeyModels();
}

async function refreshAppKeys() {
  if (!currentUser || currentUser.role !== 'admin' || !appKeyEls.list) return;
  try {
    await refreshAppKeyAccounts();
    const { keys = [] } = await appKeyRequest('GET', '/api/app-keys');
    appKeyEls.list.replaceChildren(...keys.map(appKeyRow));
    if (!keys.length) appKeyEls.list.append(appKeyElement('li', 'settings-hint', 'no app keys yet'));
  } catch (e) {
    appKeyEls.list.replaceChildren(appKeyElement('li', 'settings-hint error', e.message));
  }
  refreshAppKeyModels();
}

function optionalWholeNumber(input, what) {
  const text = input.value.trim();
  if (!text) return null;
  const number = Number(text);
  if (!Number.isInteger(number) || number < 1) throw new Error(`${what} must be a whole number above 0, or empty`);
  return number;
}

function chosenGrants() {
  const ticked = [...appKeyEls.models.querySelectorAll('input:checked')];
  const everyOf = new Set(ticked.filter(box => box.dataset.every).map(box => box.dataset.endpoint));
  return ticked
    .filter(box => box.dataset.every || !everyOf.has(box.dataset.endpoint))
    .map(box => ({ endpoint_id: box.dataset.endpoint, model: box.dataset.every ? '*' : box.value }));
}

function newAppKeyBody() {
  const grants = chosenGrants();
  if (!grants.length) throw new Error('pick at least one connection or model');
  return {
    name: appKeyEls.name.value.trim(), user_id: appKeyEls.account.value, grants,
    allow_own_tools: appKeyChoices.admin && appKeyEls.ownTools.checked,
    daily_limit: optionalWholeNumber(appKeyEls.dailyLimit, 'replies a day'),
    expires_in_days: optionalWholeNumber(appKeyEls.expiryDays, 'days to expiry'),
  };
}

if (appKeyEls.form) {
  appKeyEls.ownTools.addEventListener('change', renderAppKeyModels);
  appKeyEls.account.addEventListener('change', refreshAppKeyModels);
  appKeyEls.copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(appKeyEls.secret.value);
      showAppKeyNote('copied', 'ok');
    } catch {
      appKeyEls.secret.select();
      showAppKeyNote('select the key and copy it yourself', 'error');
    }
  };
  appKeyEls.form.addEventListener('submit', async event => {
    event.preventDefault();
    appKeyEls.shown.hidden = true;
    appKeyEls.secret.value = '';
    let body;
    try {
      body = newAppKeyBody();
    } catch (e) {
      showAppKeyNote(e.message, 'error');
      return;
    }
    if (!body.name || !body.user_id) { showAppKeyNote('give the key a name and pick an account', 'error'); return; }
    appKeyEls.submit.disabled = true;
    try {
      const made = await appKeyRequest('POST', '/api/app-keys', body);
      appKeyEls.secret.value = made.key;
      appKeyEls.shown.hidden = false;
      appKeyEls.name.value = '';
      showAppKeyNote(`made ${made.name}: copy the key now, starfire cannot show it again`, 'ok');
      refreshAppKeys();
    } catch (e) {
      showAppKeyNote(e.message, 'error');
    } finally {
      appKeyEls.submit.disabled = false;
    }
  });
}
