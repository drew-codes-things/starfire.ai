'use strict';

const API = '/api/arty-cli';
const SESSION_PARAM = 'session';
const LIST_REFRESH_MS = 5000;
const RETRY_MS = 3000;
const WAIT_SECONDS = 20;
const FORBIDDEN = 403;
const CONFLICT = 409;
const ENDED_STATUSES = new Set([404, 410]);
const MAX_OPEN_REQUESTS = 20;
const WIDE_SCREEN = '(min-width: 1100px)';
const LIVE_CLASS = 'arty-cli-live';
const MISSED_NOTE = 'Some older lines were dropped.';
const ENDED_REASON = "This session has ended on the computer, so it can't take messages.";
const BUSY_PHASES = new Set(['working', 'waiting']);

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

function showLiveLayout(isLive) {
  document.querySelector('.arty-cli-card')?.classList.toggle(LIVE_CLASS, isLive);
}

function logo() {
  return el('span', { class: 'msg-avatar' }, el('img', { src: 'assets/logo.svg', alt: '' }));
}

function spanNode(span) {
  if (span.type === 'text') return span.text;
  if (span.type === 'link') return el('a', { href: span.href, target: '_blank', rel: 'noopener noreferrer' }, span.text);
  return el(span.type, {}, span.text);
}

function lineNodes(lines) {
  return lines.flatMap((spans, index) => [index ? el('br') : null, ...spans.map(spanNode)]);
}

function markdownNodes(text) {
  return markdownBlocks(text).map((block) => {
    if (block.type === 'code') return el('pre', {}, el('code', {}, block.text));
    if (block.type === 'heading') return el('h4', {}, block.spans.map(spanNode));
    if (block.type === 'quote') return el('blockquote', {}, lineNodes(block.lines));
    if (block.type === 'list') return el(block.ordered ? 'ol' : 'ul', {}, block.items.map((spans) => el('li', {}, spans.map(spanNode))));
    return el('p', {}, lineNodes(block.lines));
  });
}

function previewNodes(text) {
  return String(text).split('\n').map((line, index) => {
    const tone = diffTone(line);
    const shownLine = index ? `\n${line}` : line;
    return tone ? el('span', { class: `arty-cli-diff-${tone}` }, shownLine) : shownLine;
  });
}

function stepView() {
  const line = el('span');
  const input = el('pre', { class: 'arty-cli-step-input' });
  const output = el('pre', { class: 'arty-cli-step-output' });
  const element = el('details', { class: 'arty-cli-step' }, el('summary', {}, line), input, output);
  let shownInput = null;
  const update = (step) => {
    line.replaceChildren(step.failed ? `${stepLine(step)} (failed)` : stepLine(step));
    element.className = step.failed ? 'arty-cli-step failed' : 'arty-cli-step';
    const readable = readableInput(step);
    if (readable !== shownInput) input.replaceChildren(...previewNodes(readable));
    shownInput = readable;
    input.hidden = !readable;
    output.replaceChildren(step.output ?? 'Still running...');
  };
  return { element, update };
}

function toolsView() {
  const summary = el('span');
  const steps = el('div', { class: 'arty-cli-steps' });
  const element = el('details', { class: 'arty-cli-tools' }, el('summary', {}, summary), steps);
  const views = [];
  const update = (item) => {
    summary.replaceChildren(toolSummary(item.steps));
    item.steps.forEach((step, index) => {
      if (!views[index]) {
        views[index] = stepView();
        steps.append(views[index].element);
      }
      views[index].update(step);
    });
  };
  return { element, update };
}

function itemView(item) {
  if (item.kind === 'tools') return toolsView();
  if (item.kind === 'compacted') {
    const summary = el('div', { class: 'arty-cli-steps' });
    const element = el('details', { class: 'arty-cli-tools arty-cli-compacted' }, el('summary', {}, COMPACTED_LINE), summary);
    return { element, update: (current) => summary.replaceChildren(...(current.text ? markdownNodes(current.text) : [NO_SUMMARY_SHOWN])) };
  }
  if (item.kind === 'note') {
    const element = el('p', { class: item.problem ? 'arty-cli-note problem' : 'arty-cli-note' }, item.text);
    return { element, update: (current) => element.replaceChildren(current.text) };
  }
  if (item.kind === 'user') {
    const element = el('div', { class: 'line-row role-user' }, el('div', { class: 'msg-head' }, item.who),
      el('div', { class: 'line user' }, item.text));
    return { element, update: () => {} };
  }
  const body = el('div', { class: 'line ai' });
  const element = el('div', { class: 'line-row role-ai' },
    el('div', { class: 'msg-head' }, logo(), el('span', { class: 'msg-model' }, 'Arty')), body);
  return { element, update: (current) => body.replaceChildren(...markdownNodes(current.text)) };
}

function transcriptView(element) {
  const views = new Map();
  const model = newTranscript();
  const show = ({ item, isNew }) => {
    if (isNew) {
      views.set(item.id, itemView(item));
      element.append(views.get(item.id).element);
    }
    views.get(item.id).update(item);
  };
  const dropOldest = () => {
    const dropped = trimTranscript(model);
    if (!dropped) return;
    element.replaceChildren(...[...element.children].slice(dropped));
    const oldestKept = model.items[0].id;
    for (const id of views.keys()) {
      if (id < oldestKept) views.delete(id);
    }
  };
  return {
    model,
    note(text) {
      show({ item: addNote(model, text), isNew: true });
      dropOldest();
    },
    add(events) {
      for (const event of events || []) {
        const firstNewId = model.nextId;
        const change = addEvent(model, event);
        for (const item of addedSince(model, firstNewId)) show({ item, isNew: true });
        if (change && !change.isNew) show(change);
      }
      dropOldest();
    },
  };
}

function permissionCard(sessionId, request, answered, readUpTo) {
  const problem = el('p', { class: 'settings-hint error' });
  let sending = false;
  const buttons = [];
  const collapse = (text) => {
    card.className = 'arty-cli-answered';
    card.replaceChildren(text);
  };
  const answer = async (allow) => {
    if (sending || answered.has(request.request_id)) return;
    sending = true;
    buttons.forEach((button) => { button.disabled = true; });
    problem.replaceChildren();
    try {
      await call('POST', `/sessions/${sessionId}/answers`, { request_id: request.request_id, allow });
      answered.set(request.request_id, readUpTo());
      collapse(allow ? 'You allowed it. Waiting for the computer...' : 'You denied it. Waiting for the computer...');
    } catch (error) {
      if (error.status === CONFLICT) {
        collapse(error.message);
        return;
      }
      problem.replaceChildren(error.message);
      buttons.forEach((button) => { button.disabled = false; });
    } finally {
      sending = false;
    }
  };
  buttons.push(el('button', { type: 'button', class: 'btn danger-fill arty-cli-big', onclick: () => answer(true) }, 'Allow'),
    el('button', { type: 'button', class: 'btn ghost arty-cli-big', onclick: () => answer(false) }, 'Deny'));
  const { heading, preview } = permissionPrompt(request);
  const card = el('section', { class: 'arty-cli-permission' }, el('p', { class: 'arty-cli-question' }, heading),
    preview ? el('pre', { class: 'arty-cli-preview' }, previewNodes(preview)) : null,
    el('div', { class: 'settings-row' }, ...buttons), problem);
  return card;
}

function shownCards(sessionId, open, cards, answered, readUpTo) {
  const waitingIds = new Set(open.map((request) => request.request_id));
  for (const requestId of [...cards.keys()]) {
    if (!waitingIds.has(requestId)) cards.delete(requestId);
  }
  for (const requestId of [...answered.keys()]) {
    if (!waitingIds.has(requestId)) answered.delete(requestId);
  }
  return open.map((request) => {
    if (!cards.has(request.request_id)) cards.set(request.request_id, permissionCard(sessionId, request, answered, readUpTo));
    return cards.get(request.request_id);
  });
}

function sessionHeader() {
  const name = el('strong', { class: 'arty-cli-name' }, 'Connecting...');
  const details = el('span', { class: 'arty-cli-details' });
  const state = el('span', { class: 'arty-cli-state' });
  const element = el('div', { class: 'arty-cli-header' }, name, details, state);
  return {
    element,
    show(session, status, tone = '') {
      if (session) {
        name.replaceChildren(sessionTitle(session));
        details.replaceChildren(sessionDetails(session));
      }
      state.replaceChildren(status);
      state.className = `arty-cli-state ${tone}`.trim();
    },
  };
}

function detailRow(label) {
  const value = el('dd');
  return { value, nodes: [el('dt', {}, label), value] };
}

function sidePanel(currentId) {
  const rows = ['Computer', 'Folder', 'Model', 'Mode', 'Connection', 'Last activity'].map(detailRow);
  const [computer, folder, model, mode, connection, lastActivity] = rows.map((row) => row.value);
  const asking = el('ul', { class: 'arty-cli-side-list' });
  const others = el('ul', { class: 'arty-cli-side-list' });
  const element = el('aside', { class: 'arty-cli-side', 'aria-label': 'Session details' },
    el('h3', {}, 'Session'), el('dl', {}, rows.flatMap((row) => row.nodes)),
    el('h3', {}, 'Waiting for you'), asking,
    el('h3', {}, 'Terminals'), others);
  return {
    element,
    show(session, status) {
      if (session) {
        computer.replaceChildren(computerName(session));
        folder.replaceChildren(String(session.folder || 'Not shared'));
        model.replaceChildren(String(session.model || 'Not shared'));
        mode.replaceChildren(String(session.mode || 'default'));
      }
      connection.replaceChildren(status);
    },
    touched() {
      lastActivity.replaceChildren(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    },
    waiting(requests) {
      asking.replaceChildren(...(requests.length
        ? requests.map((request) => el('li', {}, permissionPrompt(request).heading))
        : [el('li', { class: 'arty-cli-quiet' }, 'Nothing right now')]));
    },
    sessions(list) {
      others.replaceChildren(...list.map((session) => el('li', {}, session.id === currentId
        ? el('span', { class: 'arty-cli-current' }, sessionTitle(session))
        : el('button', { type: 'button', class: 'arty-cli-switch', onclick: () => openSession(session.id) }, sessionTitle(session),
          session.waiting_for?.length ? el('span', { class: 'arty-cli-badge' }, ' needs an answer') : null))));
    },
  };
}

function composerDock(sessionId) {
  const jump = el('button', { type: 'button', class: 'arty-cli-jump' }, 'Jump to latest');
  jump.hidden = true;
  const dots = el('span', { class: 'typing-dots', 'aria-hidden': 'true' }, el('span'), el('span'), el('span'));
  const activityText = el('span');
  const activity = el('div', { class: 'arty-cli-activity', role: 'status' }, dots, activityText);
  const problems = el('div');
  const reason = el('p', { class: 'settings-hint' });
  reason.hidden = true;
  const box = el('textarea', { rows: '1', maxlength: '8000', placeholder: 'Message Arty CLI', 'aria-label': 'Message Arty CLI' });
  const send = el('button', { type: 'submit', class: 'btn send' }, 'Send');
  const stop = el('button', { type: 'button', class: 'btn ghost', title: 'Stop what Arty is doing, like Esc on the computer' }, 'Stop');
  const form = el('form', { class: 'arty-cli-composer', novalidate: true }, box, send, stop);
  const report = (error) => problems.replaceChildren(hint(error.message, 'error'));
  const fitToText = () => {
    box.style.height = 'auto';
    box.style.height = `${composerHeight(box.scrollHeight)}px`;
  };
  box.addEventListener('input', fitToText);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = box.value.trim();
    if (!text || send.disabled) return;
    send.disabled = true;
    try {
      await call('POST', `/sessions/${sessionId}/messages`, { text });
      box.value = '';
      box.style.height = '';
      problems.replaceChildren();
    } catch (error) {
      report(error);
    } finally {
      send.disabled = box.disabled;
    }
  });
  box.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
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
  return {
    element: el('div', { class: 'arty-cli-dock' }, jump, activity, problems, reason, form),
    jump,
    problems,
    show(now) {
      activityText.replaceChildren(now.text);
      activity.className = `arty-cli-activity phase-${now.phase}`;
      dots.hidden = now.phase !== 'working';
      stop.hidden = !BUSY_PHASES.has(now.phase);
    },
    end() {
      box.disabled = true;
      box.setAttribute('placeholder', ENDED_REASON);
      send.disabled = true;
      stop.hidden = true;
      reason.replaceChildren(ENDED_REASON);
      reason.hidden = false;
    },
  };
}

function bottomFollower(scroller, jump) {
  const isAtBottom = () => isNearBottom({ scrollTop: scroller.scrollTop, scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight });
  const toBottom = () => {
    scroller.scrollTop = scroller.scrollHeight;
    jump.hidden = true;
  };
  jump.addEventListener('click', toBottom);
  scroller.addEventListener('scroll', () => {
    if (isAtBottom()) jump.hidden = true;
  }, { passive: true });
  return {
    isAtBottom,
    settle(wasAtBottom, grew) {
      if (!grew) return;
      if (wasAtBottom) toBottom();
      else jump.hidden = false;
    },
  };
}

async function watchOtherSessions(side, isCurrent) {
  while (isCurrent()) {
    if (window.matchMedia?.(WIDE_SCREEN)?.matches) {
      try {
        const { sessions } = await call('GET', '/sessions');
        if (isCurrent()) side.sessions(sessions);
      } catch (error) {
        if (error.status === FORBIDDEN) return;
      }
    }
    await pause(LIST_REFRESH_MS);
  }
}

async function followSession(page, sessionId, isCurrent) {
  showLiveLayout(true);
  const header = sessionHeader();
  const transcriptElement = el('div', { class: 'arty-cli-transcript' });
  const waiting = el('div', { class: 'arty-cli-waiting' });
  const scroller = el('div', { class: 'arty-cli-scroll' }, transcriptElement, waiting);
  const dock = composerDock(sessionId);
  const side = sidePanel(sessionId);
  const back = el('button', { type: 'button', class: 'btn ghost', onclick: () => openSession(null) }, 'All sessions');
  page.replaceChildren(el('div', { class: 'arty-cli-top' }, back, header.element),
    el('div', { class: 'arty-cli-body' }, el('div', { class: 'arty-cli-main' }, scroller, dock.element), side.element));
  const transcript = transcriptView(transcriptElement);
  const follower = bottomFollower(scroller, dock.jump);
  watchOtherSessions(side, isCurrent);
  let poll = FIRST_POLL;
  let open = [];
  let ended = false;
  let waitingCount = 0;
  let lastEventAt = Date.now();
  let recheck = null;
  const cards = new Map();
  const answered = new Map();
  const showActivity = () => dock.show(activityOf(transcript.model, { ended, waiting: waitingCount, quietMs: Date.now() - lastEventAt }));
  const finish = (status) => {
    ended = true;
    clearTimeout(recheck);
    header.show(null, status);
    side.show(null, status);
    side.waiting([]);
    waiting.replaceChildren();
    dock.end();
    showActivity();
  };
  showActivity();
  while (isCurrent()) {
    let read;
    try {
      read = await call('GET', `/sessions/${sessionId}/events?after=${poll.after}&wait=${poll.wait}`);
    } catch (error) {
      if (ENDED_STATUSES.has(error.status)) {
        finish('Session ended');
        return;
      }
      header.show(null, 'Reconnecting...', 'problem');
      side.show(null, 'Reconnecting...');
      dock.problems.replaceChildren(hint(error.message, 'error'));
      await pause(RETRY_MS);
      continue;
    }
    if (!isCurrent()) {
      clearTimeout(recheck);
      return;
    }
    dock.problems.replaceChildren();
    const wasAtBottom = follower.isAtBottom();
    const fresh = read.events || [];
    if (read.missed && poll.after) transcript.note(MISSED_NOTE);
    if (poll === FIRST_POLL || read.missed) open = read.session?.waiting_for || [];
    transcript.add(fresh);
    if (fresh.length) {
      lastEventAt = Date.now();
      side.touched();
    }
    open = openRequests(open, fresh).slice(-MAX_OPEN_REQUESTS);
    for (const requestId of askedAgain(fresh, answered)) {
      answered.delete(requestId);
      cards.delete(requestId);
    }
    poll = nextPoll(poll, read, WAIT_SECONDS);
    waiting.replaceChildren(...shownCards(sessionId, open, cards, answered, () => poll.after));
    const unanswered = open.filter((request) => !answered.has(request.request_id));
    waitingCount = unanswered.length;
    header.show(read.session, 'Live', 'live');
    side.show(read.session, 'Live');
    side.waiting(unanswered);
    showActivity();
    follower.settle(wasAtBottom, fresh.length > 0);
    clearTimeout(recheck);
    recheck = setTimeout(showActivity, FINISHED_AFTER_MS);
    if (read.ended) {
      finish('Session ended');
      return;
    }
  }
  clearTimeout(recheck);
}

async function listSessions(page, isCurrent) {
  showLiveLayout(false);
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
