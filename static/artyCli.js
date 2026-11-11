'use strict';

const API = '/api/arty-cli';
const SESSION_PARAM = 'session';
const LIST_REFRESH_MS = 5000;
const RETRY_MS = 3000;
const WAIT_SECONDS = 20;
const FORBIDDEN = 403;
const ENDED_STATUSES = new Set([404, 410]);

function call(method, path, body) {
  return callApi(API, method, path, body);
}

function pause(milliseconds) {
  return new Promise((resolve) => { setTimeout(resolve, milliseconds); });
}

function chosenSession() {
  const wanted = new URLSearchParams(window.location.search).get(SESSION_PARAM);
  return isSessionId(wanted) ? wanted : null;
}

function openSession(sessionId) {
  const address = new URL(window.location.href);
  if (sessionId) address.searchParams.set(SESSION_PARAM, sessionId);
  else address.searchParams.delete(SESSION_PARAM);
  window.history.pushState({}, '', address);
  render();
}

function logo() {
  return el('span', { class: 'msg-avatar' }, el('img', { src: 'assets/logo.svg', alt: '' }));
}

function eventLine(view) {
  if (view.kind === 'note') return el('p', { class: 'arty-cli-note' }, view.text);
  if (view.kind === 'tool') {
    return el('div', { class: 'arty-cli-tool' }, el('span', { class: 'arty-cli-tool-name' }, view.who), el('pre', {}, view.text));
  }
  const role = view.kind === 'user' ? 'role-user' : 'role-ai';
  const head = view.kind === 'ai'
    ? el('div', { class: 'msg-head' }, logo(), el('span', { class: 'msg-model' }, view.who))
    : el('div', { class: 'msg-head' }, view.who);
  return el('div', { class: `line-row ${role}` }, head, el('div', { class: 'line arty-cli-text' }, view.text));
}

function permissionCard(sessionId, request) {
  const status = el('p', { class: 'settings-hint' });
  const answer = async (allow) => {
    buttons.replaceChildren();
    try {
      await call('POST', `/sessions/${sessionId}/answers`, { request_id: request.request_id, allow });
      status.textContent = allow ? 'Allowed. Waiting for the computer...' : 'Said no. Waiting for the computer...';
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('error');
    }
  };
  const buttons = el('div', { class: 'settings-row' },
    el('button', { type: 'button', class: 'btn send', onclick: () => answer(true) }, 'Allow'),
    el('button', { type: 'button', class: 'btn ghost', onclick: () => answer(false) }, 'Deny'));
  return el('section', { class: 'settings-card arty-cli-permission' }, el('p', {}, permissionText(request)), buttons, status);
}

function composer(sessionId, problems) {
  const box = el('textarea', { rows: '2', maxlength: '8000', placeholder: 'Message Arty CLI', 'aria-label': 'Message Arty CLI' });
  const send = el('button', { type: 'submit', class: 'btn send' }, 'Send');
  const stop = el('button', { type: 'button', class: 'btn ghost', title: 'Stop what Arty is doing, like Esc on the computer' }, 'Stop');
  const form = el('form', { class: 'arty-cli-composer', novalidate: true }, box, el('div', { class: 'settings-row' }, send, stop));
  const report = (error) => problems.replaceChildren(hint(error.message, 'error'));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = box.value.trim();
    if (!text) return;
    send.disabled = true;
    try {
      await call('POST', `/sessions/${sessionId}/messages`, { text });
      box.value = '';
      problems.replaceChildren();
    } catch (error) {
      report(error);
    } finally {
      send.disabled = false;
    }
  });
  box.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  stop.addEventListener('click', async () => {
    try {
      await call('POST', `/sessions/${sessionId}/interrupt`);
    } catch (error) {
      report(error);
    }
  });
  return form;
}

async function followSession(page, sessionId, isCurrent) {
  const title = el('p', { class: 'settings-hint' }, 'Connecting...');
  const transcript = el('div', { class: 'arty-cli-transcript' });
  const waiting = el('div');
  const problems = el('div');
  const form = composer(sessionId, problems);
  const back = el('button', { type: 'button', class: 'btn ghost', onclick: () => openSession(null) }, 'All sessions');
  page.replaceChildren(el('div', { class: 'settings-row' }, back), title, transcript, waiting, problems, form);
  let after = 0;
  let pendingWaiting = [];
  const seen = [];
  const showWaiting = () => {
    waiting.replaceChildren(...openRequests(pendingWaiting, seen).map((request) => permissionCard(sessionId, request)));
  };
  while (isCurrent()) {
    let read;
    try {
      read = await call('GET', `/sessions/${sessionId}/events?after=${after}&wait=${after ? WAIT_SECONDS : 0}`);
    } catch (error) {
      if (ENDED_STATUSES.has(error.status)) {
        title.textContent = 'This session has ended.';
        form.remove();
        return;
      }
      problems.replaceChildren(hint(error.message, 'error'));
      await pause(RETRY_MS);
      continue;
    }
    if (!isCurrent()) return;
    problems.replaceChildren();
    if (read.session) {
      title.textContent = sessionLine(read.session);
      if (!after) pendingWaiting = read.session.waiting_for || [];
    }
    if (read.missed && after) transcript.append(el('p', { class: 'arty-cli-note' }, 'Some older lines were dropped.'));
    for (const event of read.events || []) {
      seen.push(event);
      const view = eventView(event);
      if (view) transcript.append(eventLine(view));
    }
    after = Number(read.next) || after;
    showWaiting();
    transcript.lastElementChild?.scrollIntoView({ block: 'end' });
    if (read.ended) {
      title.textContent = `${title.textContent} · ended`;
      form.remove();
      waiting.replaceChildren();
      return;
    }
  }
}

async function listSessions(page, isCurrent) {
  const list = el('ul', { class: 'arty-device-list' });
  page.replaceChildren(el('section', { class: 'settings-card' }, el('h3', {}, 'Live sessions'), list));
  while (isCurrent()) {
    try {
      const { sessions } = await call('GET', '/sessions');
      if (!isCurrent()) return;
      list.replaceChildren(...(sessions.length ? sessions.map((session) => el('li', {},
        el('span', { class: 'grow' }, sessionLine(session)),
        session.waiting_for?.length ? el('span', { class: 'arty-cli-badge' }, 'Needs an answer') : null,
        el('button', { type: 'button', class: 'btn send', onclick: () => openSession(session.id) }, 'Open')))
        : [el('li', {}, hint('No computer is sharing Arty CLI right now. Run /remote in Arty CLI to share one.'))]));
    } catch (error) {
      if (error.status === FORBIDDEN) {
        page.replaceChildren(hint('Only an admin can control Arty CLI. Log in with an admin account.', 'error'));
        return;
      }
      list.replaceChildren(el('li', {}, hint(error.message, 'error')));
    }
    await pause(LIST_REFRESH_MS);
  }
}

let shown = 0;

function render() {
  shown += 1;
  const mine = shown;
  const isCurrent = () => mine === shown;
  const page = document.getElementById('artyCli');
  const sessionId = chosenSession();
  if (sessionId) followSession(page, sessionId, isCurrent);
  else listSessions(page, isCurrent);
}

window.addEventListener('popstate', render);
render();
