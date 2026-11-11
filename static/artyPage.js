'use strict';

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

class RequestFailed extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function callApi(api, method, path, body) {
  const init = { method, headers: { Accept: 'application/json' } };
  if (method !== 'GET') init.headers['X-Starfire-Client'] = '1';
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`${api}${path}`, init);
  } catch {
    throw new RequestFailed(0, "Couldn't reach starfire. Check the connection and try again.");
  }
  if (response.status === 401) {
    window.location.replace(`/login.html?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    throw new RequestFailed(401, 'Log in first.');
  }
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof answer.detail === 'string' ? answer.detail : `Request failed (${response.status}).`;
    throw new RequestFailed(response.status, detail);
  }
  return answer;
}

function hint(text, tone) {
  return el('p', { class: `settings-hint${tone ? ` ${tone}` : ''}`, role: 'status' }, text);
}
