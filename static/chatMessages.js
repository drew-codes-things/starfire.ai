'use strict';

function setStatus(state, text) {
  els.dot.className          = 'dot ' + state;
  els.statusText.textContent = text;
}

function printLine(text, cls) {
  const w = $('welcome');
  if (w) w.remove();

  const div       = document.createElement('div');
  div.className   = cls ? 'line ' + cls : 'line';
  div.textContent = text;
  els.output.appendChild(div);
  els.output.scrollTop = els.output.scrollHeight;
  return div;
}

function markdownRender(text) {

  if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(text));

  return text.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

function appendMessageRow(role, idx, modelName, files, media = []) {
  const w = $('welcome');
  if (w) w.remove();

  els.output.querySelectorAll('.line-row.last').forEach(r => r.classList.remove('last'));
  const row = document.createElement('div');
  row.className = 'line-row role-' + role + ' last' + (role === 'user' && !prefs.userBubbles ? ' no-bubble' : '');
  if (role === 'ai') {
    const head = document.createElement('div');
    head.className = 'msg-head';
    head.innerHTML = '<span class="msg-avatar" aria-hidden="true"><img src="assets/logo.svg" alt="" /></span>' +
      `<span class="msg-model">${escapeHtml(modelName || 'assistant')}</span>`;
    row.appendChild(head);
  }
  if (files && files.length) {
    const fileRow = document.createElement('div');
    fileRow.className = 'msg-files';
    fileRow.innerHTML = files.map(name => `<span class="chip"><span class="chip-label">${escapeHtml(name)}</span></span>`).join('');
    row.appendChild(fileRow);
  }
  const images = media.filter(m => m.kind === 'image');
  if (images.length) {
    const imageRow = document.createElement('div');
    imageRow.className = 'msg-images';
    imageRow.innerHTML = images.map(m =>
      `<img class="msg-image" src="/api/attachments/${encodeURIComponent(m.id)}" alt="${escapeHtml(m.name)}" loading="lazy" />`).join('');
    row.appendChild(imageRow);
  }
  const content = document.createElement('div');
  content.className = 'line ' + role;
  content.dataset.msgIndex = idx;
  const actions = document.createElement('div');
  actions.className = 'line-actions';
  row.appendChild(content);
  row.appendChild(actions);
  els.output.appendChild(row);
  els.output.scrollTop = els.output.scrollHeight;
  return { row, content, actions };
}

async function copyToClipboard(btn, text) {
  if (btn.dataset.copying) return;
  btn.dataset.copying = '1';
  const original = btn.innerHTML;
  const hasLabel = !!btn.querySelector('span');
  let copied = true;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    copied = false;
  }
  btn.innerHTML = icon(copied ? 'check' : 'close') + (hasLabel ? `<span>${copied ? 'Copied' : 'Failed'}</span>` : '');
  showToast(copied ? 'Copied to clipboard' : 'Could not copy to the clipboard', copied ? 'info' : 'error');
  setTimeout(() => {
    btn.innerHTML = original;
    delete btn.dataset.copying;
  }, COPY_FEEDBACK_MS);
}

const ACTION_ICONS = {
  copy: ['copy'], edit: ['pencil'], delete: ['trash'], info: ['info'], listen: ['volume'],
  '+1': ['thumb'], '-1': ['thumb', 'flip'], continue: ['forward'], regenerate: ['refresh'],
};

function labelActionButton(btn, label, title) {
  const [name, extraClass] = ACTION_ICONS[label] || [];
  btn.innerHTML = name ? icon(name, extraClass) : escapeHtml(label);
  btn.title = title;
  btn.setAttribute('aria-label', title);
}

function addCopyButton(actions, getText) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  labelActionButton(btn, 'copy', 'Copy');
  btn.onclick = () => copyToClipboard(btn, getText());
  actions.appendChild(btn);
}

function addEditButton(actions, idx) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  labelActionButton(btn, 'edit', 'Edit and resend');
  btn.onclick = () => startEdit(idx);
  actions.appendChild(btn);
}

const PHONE_SCREEN = '(max-width: 600px)';

function regeneratePicksAndRuns() {
  return window.matchMedia(PHONE_SCREEN).matches;
}

function regenerateModelSelect() {
  const select = document.createElement('select');
  select.className = 'regen-model-select';
  select.title = 'Model to regenerate with';
  select.setAttribute('aria-label', 'Model to regenerate with');
  const heading = document.createElement('option');
  heading.value = '';
  heading.disabled = true;
  heading.textContent = REGENERATE_WITH;
  select.appendChild(heading);
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.dataset.endpointId = m.endpoint_id;
    opt.textContent = m.id;
    select.appendChild(opt);
  }
  const current = currentModel();
  select.value = current && !regeneratePicksAndRuns() ? current.id : '';
  return select;
}

function addRegenerateButton(actions, idx) {
  els.output.querySelectorAll('.regen-group').forEach(group => group.remove());
  const group = document.createElement('span');
  group.className = 'regen-group';
  const select = regenerateModelSelect();

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  labelActionButton(btn, 'regenerate', 'Regenerate with the chosen model');
  const regenerate = async () => {
    let userIdx = idx - 1;
    while (userIdx >= 0 && messages[userIdx].role !== 'user') userIdx--;
    if (userIdx < 0 || busy) return;
    const text = messages[userIdx].content;
    const overrideModel = regenerateChoice(select.selectedOptions[0]);
    await forkSession(userIdx, messages[userIdx].display ?? text);
    rerenderAll();
    send(text, overrideModel);
  };
  btn.onclick = regenerate;
  select.onchange = () => { if (regeneratePicksAndRuns()) regenerate(); };
  group.append(select, btn);
  actions.appendChild(group);
}

function speakText(text) {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function addListenButton(actions, getText) {
  if (!window.speechSynthesis) return;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  labelActionButton(btn, 'listen', 'Read aloud');
  btn.onclick = () => speakText(getText());
  actions.appendChild(btn);
}

function startEdit(idx) {
  if (busy) return;
  const original = messages[idx].display ?? messages[idx].content;
  const contentEl = els.output.querySelector(`[data-msg-index="${idx}"]`);
  if (!contentEl) return;
  contentEl.innerHTML = '';

  const ta = document.createElement('textarea');
  ta.className = 'edit-textarea';
  ta.value = original;
  contentEl.appendChild(ta);

  const controls = document.createElement('div');
  controls.className = 'edit-controls';
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button'; saveBtn.className = 'btn send'; saveBtn.textContent = 'save & resend';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button'; cancelBtn.className = 'btn ghost'; cancelBtn.textContent = 'cancel';
  controls.appendChild(saveBtn);
  controls.appendChild(cancelBtn);
  contentEl.appendChild(controls);
  autosizeEditTextarea(ta);
  ta.focus();

  cancelBtn.onclick = () => rerenderInPlace();
  saveBtn.onclick = async () => {
    const newText = ta.value.trim();
    if (!newText) return;
    await forkSession(idx, newText);
    rerenderAll();
    send(newText);
  };
}

async function forkSession(uptoIdx, textBeingSent) {
  const forkedMessages = messages.slice(0, uptoIdx);
  if (isTemporaryChat()) {
    messages = forkedMessages;
    return;
  }
  const parentId = currentSessionId;
  try {
    const r = await fetch('/api/chat/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: chatTitleFrom(forkedMessages, textBeingSent),
        messages: forkedMessages,
        parent_session_id: parentId || '',
        branch_point: uptoIdx,
      }),
    });
    if (r.ok) {
      const session = await r.json();
      currentSessionId = session.id;
      try { localStorage.setItem('sf_current_session', currentSessionId); } catch (_) {}
    }
  } catch (_) {

  }
  messages = forkedMessages;
}

const ARTY_MEDIA = { image: /\.(png|jpe?g|gif|webp)$/i, video: /\.(mp4|webm|mov|m4v)$/i };

const appConfig = { version: '', own_tools_models: ['Arty', 'assistant-core', 'arty/*'], inline_media_prefixes: ['/arty-files/'], voice_models: [] };

async function loadAppConfig() {
  try {
    const me = await fetch('/api/auth/me');
    if (me.ok) applyUserScope(await me.json());
  } catch (_) {}
  try {
    const r = await fetch('/api/config');
    if (!r.ok) return;
    const data = await r.json();
    if (Array.isArray(data.own_tools_models)) appConfig.own_tools_models = data.own_tools_models;
    if (Array.isArray(data.inline_media_prefixes)) appConfig.inline_media_prefixes = data.inline_media_prefixes;
    if (Array.isArray(data.voice_models)) appConfig.voice_models = data.voice_models;
    appConfig.version = data.version || '';
  } catch (_) {}
  const about = $('aboutVersion');
  if (about) about.textContent = 'starfire.ai' + (appConfig.version ? ' ' + appConfig.version : '');
}

let activeSpeaker = null;

function modelHasServerVoice(model) {
  return !!model && appConfig.voice_models.some(pattern => globMatch(model.id, pattern));
}

function createServerSpeaker(model, onSpeaking) {
  let pending = '';
  let queue = Promise.resolve();
  let stopped = false;
  let playing = null;

  const fetchAudio = text => fetch('/api/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: model.id, text }),
  }).then(r => (r.ok ? r.blob() : null)).catch(() => null);

  const play = blob => new Promise(resolve => {
    const url = URL.createObjectURL(blob);
    playing = new Audio(url);
    const done = () => { URL.revokeObjectURL(url); resolve(); };
    playing.onended = done;
    playing.onerror = done;
    playing.play().catch(done);
  });

  const say = sentence => {
    for (const piece of spokenPieces(speakableText(sentence))) {
      const audio = fetchAudio(piece);
      queue = queue.then(async () => {
        const blob = await audio;
        if (!blob || stopped) return;
        if (onSpeaking) onSpeaking();
        await play(blob);
      });
    }
  };

  return {
    push(delta) {
      const taken = takeSpokenSentences(pending + delta);
      taken.sentences.forEach(say);
      pending = taken.rest;
    },
    finish() {
      if (pending.trim()) say(pending);
      pending = '';
      return queue;
    },
    stop() {
      stopped = true;
      if (playing) playing.pause();
    },
  };
}

function stopServerSpeech() {
  if (activeSpeaker) activeSpeaker.stop();
  activeSpeaker = null;
}

function globMatch(value, pattern) {
  const re = new RegExp('^' + pattern.split('*').map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$', 'i');
  return re.test(value);
}

function artyFilePath(href) {
  try {
    const url = new URL(href, location.href);
    return appConfig.inline_media_prefixes.some(prefix => url.pathname.startsWith(prefix)) ? url.pathname : null;
  } catch (_) {
    return null;
  }
}

function showInlineMedia(container) {
  for (const image of container.querySelectorAll('img[src]')) {
    const path = artyFilePath(image.getAttribute('src'));
    if (path) image.src = path;
  }
  for (const link of container.querySelectorAll('a[href]')) {
    if (link.dataset.media) continue;
    const path = artyFilePath(link.getAttribute('href'));
    if (!path) continue;
    const kind = ARTY_MEDIA.video.test(path) ? 'video' : ARTY_MEDIA.image.test(path) ? 'image' : null;
    if (!kind) continue;
    link.dataset.media = kind;
    link.href = path;
    link.setAttribute('download', '');
    const media = document.createElement(kind === 'video' ? 'video' : 'img');
    media.className = 'inline-media';
    media.src = path;
    if (kind === 'video') {
      media.controls = true;
      media.preload = 'metadata';
    } else {
      media.alt = link.textContent || 'picture';
      media.loading = 'lazy';
      media.onclick = () => window.open(path, '_blank', 'noopener');
    }
    link.before(media);
  }
}

function renderAiContent(content, text, checks) {
  content.innerHTML = markdownRender(text);
  if (window.hljs) content.querySelectorAll('pre code').forEach(b => window.hljs.highlightElement(b));
  decorateCodeBlocks(content, text, checks);
  showInlineMedia(content);
}

function addActionButton(actions, label, title, onClick) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  labelActionButton(btn, label, title);
  btn.onclick = onClick;
  actions.appendChild(btn);
  return btn;
}

function addRatingButtons(actions, idx) {
  const up = addActionButton(actions, '+1', 'Good response', () => rateMessage(idx, 1));
  const down = addActionButton(actions, '-1', 'Bad response', () => rateMessage(idx, -1));
  const rating = messages[idx] ? messages[idx].rating : 0;
  up.classList.toggle('active', rating === 1);
  down.classList.toggle('active', rating === -1);
  down.classList.add('down');
}

function rateMessage(idx, value) {
  if (!messages[idx] || busy) return;
  messages[idx].rating = messages[idx].rating === value ? 0 : value;
  rerenderInPlace();
  persistSession();
}

function toggleMessageInfo(row, idx) {
  const existing = row.querySelector('.msg-info');
  if (existing) { existing.remove(); return; }
  const m = messages[idx];
  if (!m) return;
  const info = document.createElement('div');
  info.className = 'msg-info';
  const approxTokens = Math.ceil((m.content || '').length / CHARS_PER_TOKEN_ESTIMATE);
  info.textContent = [
    m.model ? 'model: ' + m.model : '',
    m.created ? new Date(m.created).toLocaleString() : '',
    '~' + approxTokens + ' tokens (estimated)',
  ].filter(Boolean).join(' · ');
  row.appendChild(info);
}

async function deleteMessage(idx) {
  if (busy || !messages[idx]) return;
  const target = messages[idx];
  const count = target.role === 'user' && messages[idx + 1] && messages[idx + 1].role === 'assistant' ? 2 : 1;
  const ok = await showDialog({
    title: count === 2 ? 'Delete this message and its reply?' : 'Delete this message?',
    text: 'This cannot be undone.', okLabel: 'Delete', danger: true,
  });
  if (!ok || busy || messages[idx] !== target) return;
  messages.splice(idx, count);
  rerenderInPlace();
  persistSession();
}

function startEditReply(idx) {
  if (busy) return;
  const contentEl = els.output.querySelector(`[data-msg-index="${idx}"]`);
  if (!contentEl) return;
  contentEl.innerHTML = '';
  const ta = document.createElement('textarea');
  ta.className = 'edit-textarea';
  ta.value = messages[idx].content;
  contentEl.appendChild(ta);
  const controls = document.createElement('div');
  controls.className = 'edit-controls';
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button'; saveBtn.className = 'btn send'; saveBtn.textContent = 'save';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button'; cancelBtn.className = 'btn ghost'; cancelBtn.textContent = 'cancel';
  controls.append(saveBtn, cancelBtn);
  contentEl.appendChild(controls);
  autosizeEditTextarea(ta);
  ta.focus();
  cancelBtn.onclick = () => rerenderInPlace();
  saveBtn.onclick = () => {
    messages[idx].content = ta.value;
    delete messages[idx].code_checks;
    rerenderInPlace();
    persistSession();
  };
}

function autosizeEditTextarea(ta) {
  const fit = () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight + 4, Math.round(window.innerHeight * 0.6)) + 'px';
  };
  ta.addEventListener('input', fit);
  fit();
}

function renderStaticMessage(role, msg, idx) {
  const text = role === 'user' ? (msg.display ?? msg.content) : msg.content;
  const { row, content, actions } = appendMessageRow(role, idx, msg.model, msg.files, msg.attachments);
  if (role === 'ai') renderAiContent(content, text, msg.code_checks);
  else content.textContent = text;
  addActionButtons(role, row, actions, idx);
}

function addActionButtons(role, row, actions, idx) {
  if (role === 'user') {
    addEditButton(actions, idx);
    addCopyButton(actions, () => messages[idx] ? (messages[idx].display ?? messages[idx].content) : '');
    addActionButton(actions, 'delete', 'Delete this message and its reply', () => deleteMessage(idx));
    return;
  }
  addActionButton(actions, 'edit', 'Edit this reply', () => startEditReply(idx));
  addCopyButton(actions, () => messages[idx] ? messages[idx].content : '');
  addListenButton(actions, () => messages[idx] ? messages[idx].content : '');
  addActionButton(actions, 'info', 'Model, time and size', () => toggleMessageInfo(row, idx));
  addRatingButtons(actions, idx);
  addActionButton(actions, 'continue', 'Continue this reply', () => continueResponse(idx));
  if (isLastAssistantMessage(messages, idx)) addRegenerateButton(actions, idx);
  addActionButton(actions, 'delete', 'Delete this reply', () => deleteMessage(idx));
}

function rerenderAll() {
  els.output.innerHTML = '';
  messages.forEach((m, idx) => {
    if (m.role === 'user') renderStaticMessage('user', m, idx);
    else if (m.role === 'assistant') renderStaticMessage('ai', m, idx);
  });
  if (!messages.length) renderWelcome();
  showOpenApprovals();
}

function rerenderInPlace() {
  const scrollTop = els.output.scrollTop;
  const wasNearBottom = els.output.scrollHeight - scrollTop - els.output.clientHeight < NEAR_BOTTOM_PX;
  rerenderAll();
  if (!wasNearBottom) els.output.scrollTop = scrollTop;
}

function welcomeBody() {
  if (models.length) {
    const model = currentModel();
    return '<p class="info-line">Local-first AI chat. How can I help?</p>' +
      `<p class="welcome-model">${escapeHtml(model ? model.id : '')}</p>`;
  }
  if (!document.body.classList.contains('is-admin')) {
    return '<p class="info-line">No models yet. Ask an admin to add a connection.</p>';
  }
  return '<p class="info-line">No models yet. Add Ollama or an API provider to start chatting.</p>' +
    '<button class="btn send" type="button" id="welcomeConnectBtn">Add a connection</button>';
}

function renderWelcome() {
  els.output.innerHTML =
    '<div class="welcome" id="welcome">' +
    '<img src="assets/logo.svg" alt="" class="welcome-logo" />' +
    '<h1 class="welcome-brand">starfire<span class="dim">.ai</span></h1>' +
    welcomeBody() +
    '</div>';
  const connect = $('welcomeConnectBtn');
  if (connect) connect.onclick = () => openSettings('providers');
  if (window.renderSuggestions) window.renderSuggestions($('suggestions'));
}

function showTypingIndicator(modelName) {
  const el = document.createElement('div');
  el.className = 'typing';
  el.setAttribute('role', 'status');
  el.innerHTML =
    '<span class="msg-avatar" aria-hidden="true"><img src="assets/logo.svg" alt="" /></span>' +
    `<span class="sr-only">${escapeHtml(modelName)} is replying</span>` +
    '<span class="typing-dots" aria-hidden="true"><span></span><span></span><span></span></span>';
  els.output.appendChild(el);
  els.output.scrollTop = els.output.scrollHeight;
  return el;
}

function setOffline(isOffline) {
  const banner = $('offlineBanner');
  if (banner) banner.hidden = !isOffline;
}

window.addEventListener('offline', () => setOffline(true));
window.addEventListener('online', () => setOffline(false));
