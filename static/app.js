'use strict';

// CSRF guard: every mutating request this page makes carries a custom
// header the server requires (see server.py's require_client_header
// middleware) — setting a custom header cross-origin forces a CORS
// preflight, which fails since this app grants no other origin CORS
// permission, so a malicious page open in another tab can't forge these
// requests. Patched onto the global fetch once here rather than passed to
// every one of this file's ~60 call sites individually.
const _rawFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const method = (init.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    init = { ...init, headers: { ...(init.headers || {}), 'X-Starfire-Client': '1' } };
  }
  return _rawFetch(input, init);
};

const $ = id => document.getElementById(id);

const els = {
  output:      $('output'),
  form:        $('form'),
  input:       $('msgInput'),
  sendBtn:     $('sendBtn'),
  stopBtn:     $('stopBtn'),
  micBtn:      $('micBtn'),
  modelList:   $('modelList'),
  ctxSize:     $('ctxSize'),
  exportBtn:   $('exportBtn'),
  clearBtn:    $('clearBtn'),
  settingsBtn: $('settingsBtn'),
  dot:         $('dot'),
  statusText:  $('statusText'),
  sysPrompt:   $('sysPrompt'),
  noModelsHint:$('noModelsHint'),
};

const settingsEls = {
  modal:          $('settingsModal'),
  closeBtn:       $('settingsCloseBtn'),
  tabs:           document.querySelectorAll('.settings-tab'),
  panels:         document.querySelectorAll('.settings-panel'),
  localBaseUrl:   $('localBaseUrl'),
  addOllamaBtn:   $('addOllamaBtn'),
  discoverBtn:    $('discoverBtn'),
  testLocalBtn:   $('testLocalBtn'),
  addLocalBtn:    $('addLocalBtn'),
  localTestResult:$('localTestResult'),
  apiProviderSelect: $('apiProviderSelect'),
  apiBaseUrl:     $('apiBaseUrl'),
  apiKeyInput:    $('apiKeyInput'),
  testApiBtn:     $('testApiBtn'),
  addApiBtn:      $('addApiBtn'),
  apiTestResult:  $('apiTestResult'),
  endpointList:   $('endpointList'),
  chatSessionList: $('chatSessionList'),
  chatSearchInput: $('chatSearchInput'),
  lightModeToggle:$('lightModeToggle'),
  filesystemPath: $('filesystemPath'),
  browseDirBtn: $('browseDirBtn'),
  dirBrowserBox: $('dirBrowserBox'),
  dirBrowserPath: $('dirBrowserPath'),
  dirBrowserList: $('dirBrowserList'),
  dirBrowserSelectBtn: $('dirBrowserSelectBtn'),
  addFilesystemBtn: $('addFilesystemBtn'),
  mcpQuickAddResult: $('mcpQuickAddResult'),
  mcpName:        $('mcpName'),
  mcpCommand:     $('mcpCommand'),
  mcpArgs:        $('mcpArgs'),
  addMcpCustomBtn:$('addMcpCustomBtn'),
  mcpCustomResult:$('mcpCustomResult'),
  mcpServerList:  $('mcpServerList'),
  toggleManageMemory:    $('toggleManageMemory'),
  toggleSearchDocuments: $('toggleSearchDocuments'),
  newMemoryText:  $('newMemoryText'),
  newMemoryCategory: $('newMemoryCategory'),
  addMemoryEntryBtn: $('addMemoryEntryBtn'),
  memoryList:     $('memoryList'),
  documentFile:   $('documentFile'),
  uploadDocumentBtn: $('uploadDocumentBtn'),
  documentUploadResult: $('documentUploadResult'),
  documentList:   $('documentList'),
  toggleManageTasks: $('toggleManageTasks'),
  toggleManageEmail: $('toggleManageEmail'),
  taskName:       $('taskName'),
  taskPrompt:     $('taskPrompt'),
  taskEndpointSelect: $('taskEndpointSelect'),
  taskSchedule:   $('taskSchedule'),
  taskTime:       $('taskTime'),
  taskDay:        $('taskDay'),
  taskCron:       $('taskCron'),
  createTaskBtn:  $('createTaskBtn'),
  taskCreateResult: $('taskCreateResult'),
  taskList:       $('taskList'),
  taskRunList:    $('taskRunList'),
  emailLabel:     $('emailLabel'),
  emailAddress:   $('emailAddress'),
  emailPassword:  $('emailPassword'),
  emailImapHost:  $('emailImapHost'),
  emailImapPort:  $('emailImapPort'),
  emailSmtpHost:  $('emailSmtpHost'),
  emailSmtpPort:  $('emailSmtpPort'),
  addEmailAccountBtn: $('addEmailAccountBtn'),
  emailAddResult: $('emailAddResult'),
  emailAccountList: $('emailAccountList'),
  emailInboxCard: $('emailInboxCard'),
  emailFolderSelect: $('emailFolderSelect'),
  emailRefreshBtn: $('emailRefreshBtn'),
  emailMessageList: $('emailMessageList'),
  emailReadingPane: $('emailReadingPane'),
  toggleManageNotes: $('toggleManageNotes'),
  toggleSearchWeb: $('toggleSearchWeb'),
  toggleGithubCli: $('toggleGithubCli'),
  toggleDeepResearch: $('toggleDeepResearch'),
  toggleEditFile: $('toggleEditFile'),
  editApprovalRow: $('editApprovalRow'),
  toggleEditApproval: $('toggleEditApproval'),
  toggleRunShell: $('toggleRunShell'),
  presetName: $('presetName'),
  savePresetBtn: $('savePresetBtn'),
  presetSaveResult: $('presetSaveResult'),
  presetList: $('presetList'),
  usageSummary: $('usageSummary'),
  hardwareInfo: $('hardwareInfo'),
  hardwareModelList: $('hardwareModelList'),
  downloadBackupBtn: $('downloadBackupBtn'),
  restoreFile: $('restoreFile'),
  restoreBackupBtn: $('restoreBackupBtn'),
  restoreResult: $('restoreResult'),
  noteTitle:       $('noteTitle'),
  noteType:        $('noteType'),
  noteContentRow:  $('noteContentRow'),
  noteContent:     $('noteContent'),
  noteItemsRow:    $('noteItemsRow'),
  noteItemsEditor: $('noteItemsEditor'),
  noteNewItem:     $('noteNewItem'),
  noteAddItemBtn:  $('noteAddItemBtn'),
  noteDueDate:     $('noteDueDate'),
  noteRepeat:      $('noteRepeat'),
  noteLabel:       $('noteLabel'),
  noteColor:       $('noteColor'),
  createNoteBtn:   $('createNoteBtn'),
  noteCreateResult:$('noteCreateResult'),
  showArchivedNotes: $('showArchivedNotes'),
  noteList:        $('noteList'),
};

let messages = [];
let busy     = false;
let models   = []; // [{id, endpoint_id, provider, size}]
let abortController = null;
let currentSessionId = null;
try { currentSessionId = localStorage.getItem('sf_current_session') || null; } catch (_) {}

let enabledMcpServers = new Set();
try { enabledMcpServers = new Set(JSON.parse(localStorage.getItem('sf_mcp_enabled') || '[]')); } catch (_) {}
function saveEnabledMcpServers() {
  try { localStorage.setItem('sf_mcp_enabled', JSON.stringify([...enabledMcpServers])); } catch (_) {}
}

let enabledBuiltinTools = new Set();
try { enabledBuiltinTools = new Set(JSON.parse(localStorage.getItem('sf_builtin_tools_enabled') || '[]')); } catch (_) {}
function saveEnabledBuiltinTools() {
  try { localStorage.setItem('sf_builtin_tools_enabled', JSON.stringify([...enabledBuiltinTools])); } catch (_) {}
}

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

function printSep() {
  const div       = document.createElement('div');
  div.className   = 'line sep';
  div.textContent = '-'.repeat(72);
  els.output.appendChild(div);
  els.output.scrollTop = els.output.scrollHeight;
}

function markdownRender(text) {
  // Sanitized with DOMPurify before it ever reaches innerHTML — marked.parse()
  // on its own passes raw HTML straight through, and the model's own output
  // isn't trustworthy input: prompt injection from a fetched web page, an
  // email body, a document, or a search result can land literal <script>/
  // onerror= payloads in the reply text, and this app has no auth boundary
  // stopping injected JS from calling any of its own APIs (memory, email,
  // shell if enabled, etc.) once it runs in this page's origin.
  if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(text));
  // Either CDN script failed to load — fail toward plain-escaped text
  // rather than ever rendering raw, unsanitized model output as HTML.
  return text.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

// ── chat message rows (copy / edit / regenerate) ────────────────────
//
// Each user/assistant message renders as a row with a small action toolbar
// (shown on hover via CSS). content.dataset.msgIndex ties the DOM node back
// to its index in `messages`, independent of whatever info/tool/separator
// lines (printLine()) are interleaved around it in the live transcript.

function appendMessageRow(role, idx) {
  const w = $('welcome');
  if (w) w.remove();

  const row = document.createElement('div');
  row.className = 'line-row';
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
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = 'copied';
  } catch (_) {
    btn.textContent = 'failed';
  }
  setTimeout(() => { btn.textContent = 'copy'; }, 1200);
}

function addCopyButton(actions, getText) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  btn.textContent = 'copy';
  btn.onclick = () => copyToClipboard(btn, getText());
  actions.appendChild(btn);
}

function addCodeBlockCopyButtons(container) {
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.code-copy-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'code-copy-btn';
    btn.textContent = 'copy';
    btn.onclick = () => {
      const code = pre.querySelector('code');
      copyToClipboard(btn, code ? code.textContent : pre.textContent);
    };
    pre.appendChild(btn);
  });
}

function addEditButton(actions, idx) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  btn.textContent = 'edit';
  btn.onclick = () => startEdit(idx);
  actions.appendChild(btn);
}

function addRegenerateButton(actions, idx) {
  // A compact model picker next to regenerate — defaults to whatever's
  // selected in the header, but letting you retry against a *different*
  // model without touching the header dropdown (and thus without also
  // starting a new chat, which changing the header selector does).
  const select = document.createElement('select');
  select.className = 'regen-model-select';
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.dataset.endpointId = m.endpoint_id;
    opt.textContent = m.id;
    select.appendChild(opt);
  }
  const current = currentModel();
  if (current) select.value = current.id;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  btn.textContent = 'regenerate';
  btn.onclick = () => {
    let userIdx = idx - 1;
    while (userIdx >= 0 && messages[userIdx].role !== 'user') userIdx--;
    if (userIdx < 0 || busy) return;
    const text = messages[userIdx].content;
    const opt = select.selectedOptions[0];
    const overrideModel = opt ? { id: opt.value, endpoint_id: opt.dataset.endpointId } : null;
    messages = messages.slice(0, userIdx);
    rerenderAll();
    send(text, overrideModel);
  };
  actions.appendChild(select);
  actions.appendChild(btn);
}

function speakText(text) {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel(); // stop whatever's currently playing first
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function addListenButton(actions, getText) {
  if (!window.speechSynthesis) return; // no TTS support — just omit the button
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  btn.textContent = 'listen';
  btn.onclick = () => speakText(getText());
  actions.appendChild(btn);
}

function diffToHtml(diffText) {
  const escape = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  return diffText.split('\n').map(line => {
    const esc = escape(line);
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) return `<span class="diff-line-meta">${esc}</span>`;
    if (line.startsWith('+')) return `<span class="diff-line-add">${esc}</span>`;
    if (line.startsWith('-')) return `<span class="diff-line-remove">${esc}</span>`;
    return `<span class="diff-line-context">${esc}</span>`;
  }).join('\n');
}

function renderDiffCard(resultJson) {
  let data;
  try { data = JSON.parse(resultJson); } catch (_) { printLine('» edit_file done', 'info'); return; }
  const w = $('welcome');
  if (w) w.remove();

  const card = document.createElement('div');
  card.className = 'diff-card';
  const status = data.staged ? 'proposed edit — awaiting approval' : data.applied ? 'applied edit' : 'edit failed';
  card.innerHTML =
    `<div class="diff-card-header">${status} — ${escapeHtml(data.path || '')}${data.error ? ' — ' + escapeHtml(data.error) : ''}</div>` +
    `<pre>${diffToHtml(data.diff || '')}</pre>`;

  if (data.staged && data.pending_id) {
    const actionsRow = document.createElement('div');
    actionsRow.className = 'diff-card-actions';
    const approveBtn = document.createElement('button');
    approveBtn.type = 'button'; approveBtn.className = 'btn send'; approveBtn.textContent = 'approve';
    const rejectBtn = document.createElement('button');
    rejectBtn.type = 'button'; rejectBtn.className = 'btn ghost'; rejectBtn.textContent = 'reject';
    approveBtn.onclick = async () => {
      approveBtn.disabled = true; rejectBtn.disabled = true;
      try {
        const r = await fetch(`/api/pending-edits/${data.pending_id}/approve`, { method: 'POST' });
        actionsRow.textContent = r.ok ? 'approved — written to disk' : 'failed to approve';
      } catch (_) { actionsRow.textContent = 'failed to approve'; }
    };
    rejectBtn.onclick = async () => {
      approveBtn.disabled = true; rejectBtn.disabled = true;
      try {
        await fetch(`/api/pending-edits/${data.pending_id}/reject`, { method: 'POST' });
        actionsRow.textContent = 'rejected — no changes written';
      } catch (_) { actionsRow.textContent = 'failed to reject'; }
    };
    actionsRow.appendChild(approveBtn);
    actionsRow.appendChild(rejectBtn);
    card.appendChild(actionsRow);
  }

  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function startEdit(idx) {
  if (busy) return;
  const original = messages[idx].content;
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
  ta.focus();

  cancelBtn.onclick = () => rerenderAll();
  saveBtn.onclick = () => {
    const newText = ta.value.trim();
    if (!newText) return;
    // Editing a message discards it and everything after it, then resends
    // the edited text as a new turn — same "edit forks the conversation"
    // behavior most chat UIs use, rather than trying to keep the old branch.
    messages = messages.slice(0, idx);
    rerenderAll();
    send(newText);
  };
}

function renderStaticMessage(role, text, idx) {
  const { content, actions } = appendMessageRow(role, idx);
  if (role === 'ai') {
    content.innerHTML = markdownRender(text);
    if (window.hljs) content.querySelectorAll('pre code').forEach(b => window.hljs.highlightElement(b));
    addCodeBlockCopyButtons(content);
  } else {
    content.textContent = text;
  }
  addCopyButton(actions, () => messages[idx] ? messages[idx].content : text);
  if (role === 'user') addEditButton(actions, idx);
  if (role === 'ai') {
    addListenButton(actions, () => messages[idx] ? messages[idx].content : text);
    addRegenerateButton(actions, idx);
  }
}

function rerenderAll() {
  els.output.innerHTML = '';
  messages.forEach((m, idx) => {
    if (m.role === 'user') renderStaticMessage('user', m.content, idx);
    else if (m.role === 'assistant') renderStaticMessage('ai', m.content, idx);
  });
  if (!messages.length) {
    els.output.innerHTML =
      '<div class="welcome" id="welcome">' +
      '<div class="welcome-brand">starfire<span class="dim">.ai</span></div>' +
      '<p class="info-line">// local-first AI chat</p>' +
      '</div>';
  }
}

// ── persisted chat sessions ──────────────────────────────────────────
//
// Every conversation autosaves to a backend session (chat_session_store.py)
// after each exchange — see persistSession(). A localStorage draft is kept
// alongside purely as a crash/offline fallback (saveDraft()); it's cleared
// once the backend save actually succeeds.

function saveDraft() {
  try { localStorage.setItem('sf_draft_messages', JSON.stringify(messages)); } catch (_) {}
}

async function persistSession() {
  const model = currentModel();
  const isNewSession = !currentSessionId;
  try {
    if (!currentSessionId) {
      const r = await fetch('/api/chat/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: (messages[0]?.content || 'New chat').slice(0, 60) }),
      });
      if (!r.ok) return;
      const session = await r.json();
      currentSessionId = session.id;
      try { localStorage.setItem('sf_current_session', currentSessionId); } catch (_) {}
    }
    await fetch('/api/chat/sessions/' + currentSessionId, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, endpoint_id: model ? model.endpoint_id : '', model: model ? model.id : '' }),
    });
    try { localStorage.removeItem('sf_draft_messages'); } catch (_) {}
    if (!settingsEls.modal.hidden) refreshSessionList();
    // Only once, right after the first exchange — the truncated-first-
    // message title is already set and usable in the meantime, so this
    // runs in the background rather than delaying the save above.
    if (isNewSession && model && messages.length >= 2) generateSessionTitle(currentSessionId, model);
  } catch (_) {
    // Backend save failed (offline/server hiccup) — saveDraft()'s
    // localStorage copy of this conversation is the fallback, so nothing
    // said so far is lost even though it isn't durably saved yet.
  }
}

async function restoreSession() {
  if (currentSessionId) {
    try {
      const r = await fetch('/api/chat/sessions/' + currentSessionId);
      if (r.ok) {
        const session = await r.json();
        messages = session.messages || [];
        rerenderAll();
        return;
      }
    } catch (_) {}
  }
  try {
    const draft = JSON.parse(localStorage.getItem('sf_draft_messages') || 'null');
    if (Array.isArray(draft) && draft.length) {
      messages = draft;
      rerenderAll();
    }
  } catch (_) {}
}

function warmModel(model) {
  if (!model) return;
  fetch('/api/warm', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id }),
  }).catch(() => {});
}

function currentModel() {
  const opt = els.modelList.selectedOptions[0];
  if (!opt) return null;
  return models.find(m => m.endpoint_id === opt.dataset.endpointId && m.id === opt.value) || null;
}

async function loadModels() {
  setStatus('loading', 'connecting...');
  try {
    const r                       = await fetch('/api/models');
    const { models: list = [] }   = await r.json();
    models = list;

    els.modelList.innerHTML = '';
    for (const m of models) {
      const opt            = document.createElement('option');
      opt.value             = m.id;
      opt.dataset.endpointId = m.endpoint_id;
      const label           = window.providerLabel ? window.providerLabel(m.provider) : m.provider;
      opt.textContent        = m.id + (m.size ? ' (' + (m.size / 1073741824).toFixed(1) + ' GB)' : '') + ' — ' + label;
      els.modelList.appendChild(opt);
    }

    if (!models.length) {
      setStatus('error', 'no models configured');
      if (els.noModelsHint) els.noModelsHint.style.display = '';
      els.input.disabled   = true;
      els.sendBtn.disabled = true;
      return;
    }

    if (els.noModelsHint) els.noModelsHint.style.display = 'none';
    setStatus('ready', els.modelList.value);
    warmModel(currentModel());
    els.input.disabled   = false;
    els.sendBtn.disabled = false;
    els.input.focus();
  } catch (e) {
    setStatus('error', 'cannot reach starfire backend');
    printLine('error: ' + e.message, 'err');
  }
}

async function send(text, overrideModel) {
  if (busy) return;
  const model = overrideModel || currentModel();
  if (!model) return;

  busy = true;
  els.sendBtn.disabled = true;
  els.input.disabled   = true;
  els.stopBtn.hidden    = false;
  abortController       = new AbortController();

  const userIdx = messages.length;
  messages.push({ role: 'user', content: text });
  const userRow = appendMessageRow('user', userIdx);
  userRow.content.textContent = text;
  addCopyButton(userRow.actions, () => messages[userIdx] ? messages[userIdx].content : text);
  addEditButton(userRow.actions, userIdx);
  saveDraft();

  // aiLine/renderer are created lazily on the first text delta, so any
  // tool_start/tool_result activity prints as its own info line *before*
  // the assistant's reply bubble appears, in chronological order.
  let aiLine = null;
  let aiActions = null;
  let renderer = null;
  let gotToken = false;
  const aiIdx = messages.length; // where the assistant reply will land, once pushed below

  setStatus('loading', 'waiting...');

  try {
    // Sends the full session — context_budget.py on the backend trims to
    // the model's actual context window (from the ctx-size selector below)
    // rather than the frontend guessing a fixed message count.
    const r = await fetch('/api/chat_stream', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      signal:  abortController.signal,
      body:    JSON.stringify({
        endpoint_id: model.endpoint_id,
        model:       model.id,
        messages:    messages.slice(),
        system:      els.sysPrompt.value.trim() || undefined,
        options:     { num_ctx: parseInt(els.ctxSize?.value || '2048', 10) },
        enabled_mcp_servers: [...enabledMcpServers],
        enabled_builtin_tools: [...enabledBuiltinTools],
        require_edit_approval: requireEditApproval,
      }),
    });

    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'server error ' + r.status);
    }

    const reader = r.body.getReader();
    const dec    = new TextDecoder();
    let   buf    = '';
    let   streamError = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const obj = JSON.parse(line.slice(5).trim());
          if (obj.delta) {
            if (!gotToken) { gotToken = true; setStatus('ready', model.id); }
            if (!aiLine) {
              const created = appendMessageRow('ai', aiIdx);
              aiLine = created.content; aiActions = created.actions;
              aiLine.classList.add('streaming');
              renderer = createStreamRenderer(aiLine, { render: markdownRender });
            }
            renderer.push(obj.delta);
            els.output.scrollTop = els.output.scrollHeight;
          } else if (obj.tool_start) {
            printLine('» calling ' + obj.tool_start.replace(/^mcp__[^_]+__/, '') + '…', 'info');
          } else if (obj.tool_result) {
            if (obj.tool_result.name === 'edit_file') {
              renderDiffCard(obj.tool_result.result);
            } else {
              printLine('» ' + obj.tool_result.name.replace(/^mcp__[^_]+__/, '') + ' done', 'info');
            }
          } else if (obj.error) {
            streamError = obj.error;
          }
        } catch (_) {}
      }
    }

    if (streamError) throw new Error(streamError);

    if (aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      addCodeBlockCopyButtons(aiLine);
    }
    setStatus('ready', model.id);
    const fullText = renderer ? renderer.text() : '';
    if (fullText) {
      messages.push({ role: 'assistant', content: fullText });
      if (aiActions) {
        addCopyButton(aiActions, () => messages[aiIdx] ? messages[aiIdx].content : fullText);
        addListenButton(aiActions, () => messages[aiIdx] ? messages[aiIdx].content : fullText);
        addRegenerateButton(aiActions, aiIdx);
      }
    }
    printSep();
    persistSession();
  } catch (e) {
    const aborted = e.name === 'AbortError';
    if (gotToken && aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      addCodeBlockCopyButtons(aiLine);
      const fullText = renderer.text();
      if (fullText) {
        messages.push({ role: 'assistant', content: fullText });
        if (aiActions) {
          addCopyButton(aiActions, () => messages[aiIdx] ? messages[aiIdx].content : fullText);
          addRegenerateButton(aiActions, aiIdx);
        }
      }
    } else if (!aborted) {
      messages.splice(userIdx, 1);
      rerenderAll();
    }
    if (aborted) {
      setStatus('ready', model.id);
      printLine('— stopped —', 'info');
      persistSession();
    } else {
      setStatus('error', 'error');
      printLine('error: ' + e.message, 'err');
    }
  } finally {
    busy                   = false;
    abortController        = null;
    els.input.disabled     = false;
    els.sendBtn.disabled   = false;
    els.stopBtn.hidden      = true;
    els.input.value        = '';
    els.input.style.height = 'auto';
    els.input.focus();
  }
}

function exportChat() {
  if (!messages.length) return;
  const text = messages.map(m => '### ' + m.role + '\n\n' + m.content).join('\n\n---\n\n');
  const url  = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const a    = Object.assign(document.createElement('a'), { href: url, download: 'starfire-' + Date.now() + '.md' });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

function clearChat() {
  messages = [];
  currentSessionId = null;
  try {
    localStorage.removeItem('sf_current_session');
    localStorage.removeItem('sf_draft_messages');
  } catch (_) {}
  rerenderAll();
}

els.stopBtn.onclick = () => { if (abortController) abortController.abort(); };

els.modelList.addEventListener('change', () => {
  clearChat();
  warmModel(currentModel());
  setStatus('ready', els.modelList.value);
});

els.exportBtn.onclick = exportChat;
els.clearBtn.onclick  = clearChat;

els.form.addEventListener('submit', e => {
  e.preventDefault();
  const t = els.input.value.trim();
  if (t) send(t);
});

els.input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, 140) + 'px';
});

try { els.sysPrompt.value = localStorage.getItem('sf_sys') || ''; } catch (_) {}
els.sysPrompt.addEventListener('input', () => {
  try { localStorage.setItem('sf_sys', els.sysPrompt.value); } catch (_) {}
});

// ── settings modal ──────────────────────────────────────────────────

function openSettings() {
  settingsEls.modal.hidden = false;
  refreshSessionList();
  refreshEndpointList();
  refreshMcpServerList();
  refreshMemoryList();
  refreshDocumentList();
  refreshTaskEndpointOptions();
  refreshTaskList();
  refreshTaskRunList();
  refreshEmailAccountList();
  refreshNoteList();
  startNoteReminderTimer();
  refreshPresetList();
  refreshUsage();
  refreshHardware();
}
function closeSettings() {
  settingsEls.modal.hidden = true;
  stopNoteReminderTimer();
}

els.settingsBtn.onclick        = openSettings;
settingsEls.closeBtn.onclick   = closeSettings;
settingsEls.modal.addEventListener('click', e => {
  if (e.target === settingsEls.modal) closeSettings();
});

settingsEls.tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    settingsEls.tabs.forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    settingsEls.panels.forEach(p => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  });
});

async function refreshEndpointList() {
  try {
    const r = await fetch('/api/model-endpoints');
    const { endpoints = [] } = await r.json();
    settingsEls.endpointList.innerHTML = '';
    if (!endpoints.length) {
      settingsEls.endpointList.innerHTML = '<li class="settings-hint">none configured yet</li>';
      return;
    }
    for (const ep of endpoints) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(ep.label || ep.base_url)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(ep.provider)}</span>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/model-endpoints/' + ep.id, { method: 'DELETE' });
        await refreshEndpointList();
        await loadModels();
      };
      settingsEls.endpointList.appendChild(li);
    }
  } catch (_) {
    settingsEls.endpointList.innerHTML = '<li class="settings-hint error">failed to load endpoints</li>';
  }
}

settingsEls.addOllamaBtn.onclick = async () => {
  try {
    const r = await fetch('/api/config');
    const { ollama_base_url } = await r.json();
    settingsEls.localBaseUrl.value = ollama_base_url || 'http://localhost:11434';
  } catch (_) {
    settingsEls.localBaseUrl.value = 'http://localhost:11434';
  }
  settingsEls.localBaseUrl.focus();
};

settingsEls.discoverBtn.onclick = async () => {
  settingsEls.localTestResult.textContent = 'scanning…';
  settingsEls.localTestResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/discover');
    const { found = [] } = await r.json();
    if (!found.length) {
      settingsEls.localTestResult.textContent = 'no local model servers found';
      return;
    }
    for (const item of found) {
      await fetch('/api/model-endpoints', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: item.base_url, kind: 'ollama', label: item.label }),
      }).catch(() => {});
    }
    settingsEls.localTestResult.textContent = `found and added ${found.length} server(s)`;
    settingsEls.localTestResult.className = 'settings-hint ok';
    await refreshEndpointList();
    await loadModels();
  } catch (e) {
    settingsEls.localTestResult.textContent = 'scan failed: ' + e.message;
    settingsEls.localTestResult.className = 'settings-hint error';
  }
};

settingsEls.testLocalBtn.onclick = async () => {
  const base_url = settingsEls.localBaseUrl.value.trim();
  if (!base_url) return;
  settingsEls.localTestResult.textContent = 'testing…';
  settingsEls.localTestResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/model-endpoints/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url }),
    });
    const { ok, message } = await r.json();
    settingsEls.localTestResult.textContent = message;
    settingsEls.localTestResult.className = 'settings-hint ' + (ok ? 'ok' : 'error');
  } catch (e) {
    settingsEls.localTestResult.textContent = 'test failed: ' + e.message;
    settingsEls.localTestResult.className = 'settings-hint error';
  }
};

settingsEls.addLocalBtn.onclick = async () => {
  const base_url = settingsEls.localBaseUrl.value.trim();
  if (!base_url) return;
  try {
    const r = await fetch('/api/model-endpoints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url, kind: 'ollama' }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'add failed');
    }
    settingsEls.localTestResult.textContent = 'added';
    settingsEls.localTestResult.className = 'settings-hint ok';
    settingsEls.localBaseUrl.value = '';
    await refreshEndpointList();
    await loadModels();
  } catch (e) {
    settingsEls.localTestResult.textContent = e.message;
    settingsEls.localTestResult.className = 'settings-hint error';
  }
};

settingsEls.apiProviderSelect.addEventListener('change', () => {
  const opt = settingsEls.apiProviderSelect.selectedOptions[0];
  settingsEls.apiBaseUrl.value = opt.dataset.url || '';
});
settingsEls.apiBaseUrl.value = settingsEls.apiProviderSelect.selectedOptions[0]?.dataset.url || '';

settingsEls.testApiBtn.onclick = async () => {
  const base_url = settingsEls.apiBaseUrl.value.trim();
  const api_key  = settingsEls.apiKeyInput.value.trim();
  if (!base_url) return;
  settingsEls.apiTestResult.textContent = 'testing…';
  settingsEls.apiTestResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/model-endpoints/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url, api_key: api_key || undefined }),
    });
    const { ok, message } = await r.json();
    settingsEls.apiTestResult.textContent = message;
    settingsEls.apiTestResult.className = 'settings-hint ' + (ok ? 'ok' : 'error');
  } catch (e) {
    settingsEls.apiTestResult.textContent = 'test failed: ' + e.message;
    settingsEls.apiTestResult.className = 'settings-hint error';
  }
};

settingsEls.addApiBtn.onclick = async () => {
  const base_url = settingsEls.apiBaseUrl.value.trim();
  const api_key  = settingsEls.apiKeyInput.value.trim();
  if (!base_url) return;
  const providerName = settingsEls.apiProviderSelect.selectedOptions[0]?.textContent || base_url;
  try {
    const r = await fetch('/api/model-endpoints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url, api_key: api_key || undefined, kind: 'api-key', label: providerName }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'add failed');
    }
    settingsEls.apiTestResult.textContent = 'added';
    settingsEls.apiTestResult.className = 'settings-hint ok';
    settingsEls.apiKeyInput.value = '';
    await refreshEndpointList();
    await loadModels();
  } catch (e) {
    settingsEls.apiTestResult.textContent = e.message;
    settingsEls.apiTestResult.className = 'settings-hint error';
  }
};

// ── MCP tools ────────────────────────────────────────────────────────

async function refreshMcpServerList() {
  try {
    const r = await fetch('/api/mcp/servers');
    const { servers = [] } = await r.json();
    settingsEls.mcpServerList.innerHTML = '';
    if (!servers.length) {
      settingsEls.mcpServerList.innerHTML = '<li class="settings-hint">none configured yet</li>';
      return;
    }
    // Drop enabled-set entries for servers that no longer exist.
    const liveIds = new Set(servers.map(s => s.id));
    for (const id of [...enabledMcpServers]) if (!liveIds.has(id)) enabledMcpServers.delete(id);
    saveEnabledMcpServers();

    for (const s of servers) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const checked = enabledMcpServers.has(s.id) ? 'checked' : '';
      li.innerHTML =
        `<label class="theme-toggle"><input type="checkbox" ${checked} /></label>` +
        `<span class="endpoint-label">${escapeHtml(s.name)}</span>` +
        `<span class="endpoint-meta">${s.tool_count} tool(s)${s.connected ? '' : ' · not running'}</span>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('input[type="checkbox"]').onchange = e => {
        if (e.target.checked) enabledMcpServers.add(s.id); else enabledMcpServers.delete(s.id);
        saveEnabledMcpServers();
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/mcp/servers/' + s.id, { method: 'DELETE' });
        enabledMcpServers.delete(s.id);
        saveEnabledMcpServers();
        await refreshMcpServerList();
      };
      settingsEls.mcpServerList.appendChild(li);
    }
  } catch (_) {
    settingsEls.mcpServerList.innerHTML = '<li class="settings-hint error">failed to load MCP servers</li>';
  }
}

async function addMcpPreset(preset, resultEl, path) {
  resultEl.textContent = 'starting…';
  resultEl.className = 'settings-hint';
  try {
    const r = await fetch('/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset, path: path || undefined }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'failed to start');
    }
    const server = await r.json();
    enabledMcpServers.add(server.id);
    saveEnabledMcpServers();
    resultEl.textContent = `added — ${server.tool_count} tool(s) available`;
    resultEl.className = 'settings-hint ok';
    await refreshMcpServerList();
  } catch (e) {
    resultEl.textContent = e.message;
    resultEl.className = 'settings-hint error';
  }
}

settingsEls.addFilesystemBtn.onclick = () =>
  addMcpPreset('filesystem', settingsEls.mcpQuickAddResult, settingsEls.filesystemPath.value.trim());

// ── directory browser (pick the filesystem MCP server's folder by
// clicking, rather than typing an absolute path from memory) ──────────

let dirBrowserCurrent = '';

async function loadDirBrowser(path) {
  try {
    const url = '/api/browse-dir' + (path ? '?path=' + encodeURIComponent(path) : '');
    const r = await fetch(url);
    if (!r.ok) return;
    const data = await r.json();
    dirBrowserCurrent = data.path;
    settingsEls.dirBrowserPath.textContent = data.path;
    settingsEls.dirBrowserList.innerHTML = '';
    if (data.parent) {
      const up = document.createElement('li');
      up.className = 'endpoint-row';
      up.innerHTML = '<span class="endpoint-label">.. (up one level)</span>';
      up.onclick = () => loadDirBrowser(data.parent);
      settingsEls.dirBrowserList.appendChild(up);
    }
    if (!data.entries.length && !data.parent) {
      settingsEls.dirBrowserList.innerHTML += '<li class="settings-hint">no subfolders here</li>';
    }
    for (const entry of data.entries) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML = `<span class="endpoint-label">📁 ${escapeHtml(entry.name)}</span>`;
      li.onclick = () => loadDirBrowser(entry.path);
      settingsEls.dirBrowserList.appendChild(li);
    }
  } catch (_) {
    settingsEls.dirBrowserList.innerHTML = '<li class="settings-hint error">failed to browse</li>';
  }
}

settingsEls.browseDirBtn.onclick = () => {
  const wasHidden = settingsEls.dirBrowserBox.hidden;
  settingsEls.dirBrowserBox.hidden = !wasHidden;
  if (wasHidden) loadDirBrowser(settingsEls.filesystemPath.value.trim());
};

settingsEls.dirBrowserSelectBtn.onclick = () => {
  settingsEls.filesystemPath.value = dirBrowserCurrent;
  settingsEls.dirBrowserBox.hidden = true;
};

settingsEls.addMcpCustomBtn.onclick = async () => {
  const name = settingsEls.mcpName.value.trim();
  const command = settingsEls.mcpCommand.value.trim();
  const args = settingsEls.mcpArgs.value.trim().split(/\s+/).filter(Boolean);
  if (!command) return;
  settingsEls.mcpCustomResult.textContent = 'starting…';
  settingsEls.mcpCustomResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name || command, command, args }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'failed to start');
    }
    const server = await r.json();
    enabledMcpServers.add(server.id);
    saveEnabledMcpServers();
    settingsEls.mcpCustomResult.textContent = `added — ${server.tool_count} tool(s) available`;
    settingsEls.mcpCustomResult.className = 'settings-hint ok';
    settingsEls.mcpName.value = '';
    settingsEls.mcpCommand.value = '';
    settingsEls.mcpArgs.value = '';
    await refreshMcpServerList();
  } catch (e) {
    settingsEls.mcpCustomResult.textContent = e.message;
    settingsEls.mcpCustomResult.className = 'settings-hint error';
  }
};

// ── built-in tool toggles ───────────────────────────────────────────

settingsEls.toggleManageMemory.checked = enabledBuiltinTools.has('manage_memory');
settingsEls.toggleManageMemory.addEventListener('change', () => {
  if (settingsEls.toggleManageMemory.checked) enabledBuiltinTools.add('manage_memory');
  else enabledBuiltinTools.delete('manage_memory');
  saveEnabledBuiltinTools();
});

settingsEls.toggleSearchDocuments.checked = enabledBuiltinTools.has('search_documents');
settingsEls.toggleSearchDocuments.addEventListener('change', () => {
  if (settingsEls.toggleSearchDocuments.checked) enabledBuiltinTools.add('search_documents');
  else enabledBuiltinTools.delete('search_documents');
  saveEnabledBuiltinTools();
});

settingsEls.toggleManageTasks.checked = enabledBuiltinTools.has('manage_tasks');
settingsEls.toggleManageTasks.addEventListener('change', () => {
  if (settingsEls.toggleManageTasks.checked) enabledBuiltinTools.add('manage_tasks');
  else enabledBuiltinTools.delete('manage_tasks');
  saveEnabledBuiltinTools();
});

settingsEls.toggleManageEmail.checked = enabledBuiltinTools.has('manage_email');
settingsEls.toggleManageEmail.addEventListener('change', () => {
  if (settingsEls.toggleManageEmail.checked) enabledBuiltinTools.add('manage_email');
  else enabledBuiltinTools.delete('manage_email');
  saveEnabledBuiltinTools();
});

settingsEls.toggleManageNotes.checked = enabledBuiltinTools.has('manage_notes');
settingsEls.toggleManageNotes.addEventListener('change', () => {
  if (settingsEls.toggleManageNotes.checked) enabledBuiltinTools.add('manage_notes');
  else enabledBuiltinTools.delete('manage_notes');
  saveEnabledBuiltinTools();
});

settingsEls.toggleSearchWeb.checked = enabledBuiltinTools.has('search_web');
settingsEls.toggleSearchWeb.addEventListener('change', () => {
  if (settingsEls.toggleSearchWeb.checked) enabledBuiltinTools.add('search_web');
  else enabledBuiltinTools.delete('search_web');
  saveEnabledBuiltinTools();
});

settingsEls.toggleGithubCli.checked = enabledBuiltinTools.has('github_cli');
settingsEls.toggleGithubCli.addEventListener('change', () => {
  if (settingsEls.toggleGithubCli.checked) enabledBuiltinTools.add('github_cli');
  else enabledBuiltinTools.delete('github_cli');
  saveEnabledBuiltinTools();
});

settingsEls.toggleDeepResearch.checked = enabledBuiltinTools.has('deep_research');
settingsEls.toggleDeepResearch.addEventListener('change', () => {
  if (settingsEls.toggleDeepResearch.checked) enabledBuiltinTools.add('deep_research');
  else enabledBuiltinTools.delete('deep_research');
  saveEnabledBuiltinTools();
});

settingsEls.toggleEditFile.checked = enabledBuiltinTools.has('edit_file');
settingsEls.editApprovalRow.hidden = !settingsEls.toggleEditFile.checked;
settingsEls.toggleEditFile.addEventListener('change', () => {
  if (settingsEls.toggleEditFile.checked) enabledBuiltinTools.add('edit_file');
  else enabledBuiltinTools.delete('edit_file');
  settingsEls.editApprovalRow.hidden = !settingsEls.toggleEditFile.checked;
  saveEnabledBuiltinTools();
});

let requireEditApproval = false;
try { requireEditApproval = localStorage.getItem('sf_edit_approval') === '1'; } catch (_) {}
settingsEls.toggleEditApproval.checked = requireEditApproval;
settingsEls.toggleEditApproval.addEventListener('change', () => {
  requireEditApproval = settingsEls.toggleEditApproval.checked;
  try { localStorage.setItem('sf_edit_approval', requireEditApproval ? '1' : '0'); } catch (_) {}
});

settingsEls.toggleRunShell.checked = enabledBuiltinTools.has('run_shell');
settingsEls.toggleRunShell.addEventListener('change', () => {
  if (settingsEls.toggleRunShell.checked) enabledBuiltinTools.add('run_shell');
  else enabledBuiltinTools.delete('run_shell');
  saveEnabledBuiltinTools();
});

// ── saved chat sessions (Chats tab) ─────────────────────────────────

async function refreshSessionList() {
  const q = settingsEls.chatSearchInput ? settingsEls.chatSearchInput.value.trim() : '';
  try {
    const url = '/api/chat/sessions' + (q ? '?q=' + encodeURIComponent(q) : '');
    const r = await fetch(url);
    const { sessions = [] } = await r.json();
    settingsEls.chatSessionList.innerHTML = '';
    if (!sessions.length) {
      settingsEls.chatSessionList.innerHTML = `<li class="settings-hint">${q ? 'no matching chats' : 'no saved chats yet'}</li>`;
      return;
    }
    for (const s of sessions) {
      const li = document.createElement('li');
      li.className = 'endpoint-row' + (s.id === currentSessionId ? ' active-session' : '');
      const pinIcon = s.pinned ? '📌 ' : '';
      li.innerHTML =
        `<span class="endpoint-label">${pinIcon}${escapeHtml(s.title || '(untitled)')}</span>` +
        `<span class="endpoint-meta">${s.message_count} msg &middot; ${new Date(s.updated).toLocaleString()}</span>` +
        `<button class="btn ghost" data-act="pin">${s.pinned ? 'unpin' : 'pin'}</button>` +
        `<button class="btn ghost" data-act="open">open</button>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('[data-act="pin"]').onclick = async () => {
        await fetch('/api/chat/sessions/' + s.id, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pinned: !s.pinned }),
        });
        await refreshSessionList();
      };
      li.querySelector('[data-act="open"]').onclick = () => openSession(s.id);
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/chat/sessions/' + s.id, { method: 'DELETE' });
        if (currentSessionId === s.id) clearChat();
        await refreshSessionList();
      };
      settingsEls.chatSessionList.appendChild(li);
    }
  } catch (_) {
    settingsEls.chatSessionList.innerHTML = '<li class="settings-hint error">failed to load chats</li>';
  }
}

async function openSession(id) {
  if (busy) return;
  try {
    const r = await fetch('/api/chat/sessions/' + id);
    if (!r.ok) return;
    const session = await r.json();
    currentSessionId = id;
    try { localStorage.setItem('sf_current_session', id); } catch (_) {}
    messages = session.messages || [];
    rerenderAll();
    closeSettings();
  } catch (_) {}
}

// ── memory ───────────────────────────────────────────────────────────

async function refreshMemoryList() {
  try {
    const r = await fetch('/api/memory');
    const { memories = [] } = await r.json();
    settingsEls.memoryList.innerHTML = '';
    if (!memories.length) {
      settingsEls.memoryList.innerHTML = '<li class="settings-hint">nothing remembered yet</li>';
      return;
    }
    for (const m of memories) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const pinChecked = m.pinned ? 'checked' : '';
      li.innerHTML =
        `<label class="theme-toggle" title="pin"><input type="checkbox" ${pinChecked} /></label>` +
        `<span class="endpoint-label">${escapeHtml(m.text)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(m.category)}</span>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('input[type="checkbox"]').onchange = async e => {
        await fetch('/api/memory/' + m.id + '/pin', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pinned: e.target.checked }),
        });
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/memory/' + m.id, { method: 'DELETE' });
        await refreshMemoryList();
      };
      settingsEls.memoryList.appendChild(li);
    }
  } catch (_) {
    settingsEls.memoryList.innerHTML = '<li class="settings-hint error">failed to load memory</li>';
  }
}

settingsEls.addMemoryEntryBtn.onclick = async () => {
  const text = settingsEls.newMemoryText.value.trim();
  if (!text) return;
  await fetch('/api/memory', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, category: settingsEls.newMemoryCategory.value }),
  });
  settingsEls.newMemoryText.value = '';
  await refreshMemoryList();
};

// ── documents ────────────────────────────────────────────────────────

async function refreshDocumentList() {
  try {
    const r = await fetch('/api/documents');
    const { documents = [] } = await r.json();
    settingsEls.documentList.innerHTML = '';
    if (!documents.length) {
      settingsEls.documentList.innerHTML = '<li class="settings-hint">none uploaded yet</li>';
      return;
    }
    for (const d of documents) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(d.filename)}</span>` +
        `<span class="endpoint-meta">${d.chunk_count} chunk(s)</span>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/documents/' + d.id, { method: 'DELETE' });
        await refreshDocumentList();
      };
      settingsEls.documentList.appendChild(li);
    }
  } catch (_) {
    settingsEls.documentList.innerHTML = '<li class="settings-hint error">failed to load documents</li>';
  }
}

settingsEls.uploadDocumentBtn.onclick = async () => {
  const file = settingsEls.documentFile.files[0];
  if (!file) return;
  settingsEls.documentUploadResult.textContent = 'uploading…';
  settingsEls.documentUploadResult.className = 'settings-hint';
  try {
    const formData = new FormData();
    formData.append('file', file);
    const r = await fetch('/api/documents', { method: 'POST', body: formData });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'upload failed');
    }
    const doc = await r.json();
    settingsEls.documentUploadResult.textContent = `indexed — ${doc.chunk_count} chunk(s)`;
    settingsEls.documentUploadResult.className = 'settings-hint ok';
    settingsEls.documentFile.value = '';
    await refreshDocumentList();
  } catch (e) {
    settingsEls.documentUploadResult.textContent = e.message;
    settingsEls.documentUploadResult.className = 'settings-hint error';
  }
};

// ── automations (scheduled tasks) ───────────────────────────────────

function refreshTaskEndpointOptions() {
  settingsEls.taskEndpointSelect.innerHTML = '';
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.dataset.endpointId = m.endpoint_id;
    opt.textContent = m.id + ' (' + (window.providerLabel ? window.providerLabel(m.provider) : m.provider) + ')';
    settingsEls.taskEndpointSelect.appendChild(opt);
  }
}

// Same tool set the chat toggles offer, as checkboxes on the task-creation
// form — a task is "send a prompt, with these tools available", so it needs
// its own tool selection independent of what's enabled for live chat.
const TASK_TOOL_OPTIONS = [
  ['manage_memory', 'memory'], ['search_documents', 'documents'], ['manage_tasks', 'tasks'],
  ['manage_email', 'email'], ['manage_notes', 'notes'], ['search_web', 'web search'],
  ['run_shell', '⚠️ shell'],
];

function renderTaskToolCheckboxes() {
  const box = $('taskToolCheckboxes');
  if (!box || box.childElementCount) return; // build once
  for (const [id, label] of TASK_TOOL_OPTIONS) {
    const wrap = document.createElement('label');
    wrap.className = 'theme-toggle';
    const input = document.createElement('input');
    input.type = 'checkbox'; input.value = id;
    wrap.appendChild(input);
    wrap.appendChild(document.createTextNode(label));
    box.appendChild(wrap);
  }
}
renderTaskToolCheckboxes();

async function refreshTaskList() {
  try {
    const r = await fetch('/api/tasks');
    const { tasks = [] } = await r.json();
    settingsEls.taskList.innerHTML = '';
    if (!tasks.length) {
      settingsEls.taskList.innerHTML = '<li class="settings-hint">no scheduled tasks yet</li>';
      return;
    }
    for (const t of tasks) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const scheduleLabel = t.schedule === 'cron' ? t.cron_expression : t.schedule + (t.scheduled_time ? ' ' + t.scheduled_time : '');
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(t.name)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(scheduleLabel)} · ${escapeHtml(t.status)} · next ${t.next_run ? new Date(t.next_run).toLocaleString() : '—'}</span>` +
        `<button class="btn ghost" data-act="toggle">${t.status === 'active' ? 'pause' : 'resume'}</button>` +
        `<button class="btn ghost" data-act="run">run now</button>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('[data-act="toggle"]').onclick = async () => {
        await fetch(`/api/tasks/${t.id}/${t.status === 'active' ? 'pause' : 'resume'}`, { method: 'POST' });
        await refreshTaskList();
      };
      li.querySelector('[data-act="run"]').onclick = async () => {
        await fetch(`/api/tasks/${t.id}/run`, { method: 'POST' });
        await refreshTaskList();
        await refreshTaskRunList();
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/tasks/' + t.id, { method: 'DELETE' });
        await refreshTaskList();
      };
      settingsEls.taskList.appendChild(li);
    }
  } catch (_) {
    settingsEls.taskList.innerHTML = '<li class="settings-hint error">failed to load tasks</li>';
  }
}

async function refreshTaskRunList() {
  try {
    const r = await fetch('/api/tasks/runs/recent');
    const { runs = [] } = await r.json();
    settingsEls.taskRunList.innerHTML = '';
    if (!runs.length) {
      settingsEls.taskRunList.innerHTML = '<li class="settings-hint">no runs yet</li>';
      return;
    }
    for (const run of runs.slice(0, 15)) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const summary = (run.output || '').slice(0, 80);
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(summary)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(run.status)} · ${new Date(run.started).toLocaleString()}</span>`;
      settingsEls.taskRunList.appendChild(li);
    }
  } catch (_) {
    settingsEls.taskRunList.innerHTML = '<li class="settings-hint error">failed to load runs</li>';
  }
}

settingsEls.createTaskBtn.onclick = async () => {
  const prompt = settingsEls.taskPrompt.value.trim();
  const schedule = settingsEls.taskSchedule.value;
  if (!prompt) return;
  const opt = settingsEls.taskEndpointSelect.selectedOptions[0];
  const enabled_builtin_tools = [...document.querySelectorAll('#taskToolCheckboxes input:checked')].map(i => i.value);
  try {
    const r = await fetch('/api/tasks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: settingsEls.taskName.value.trim(), prompt, schedule,
        scheduled_time: settingsEls.taskTime.value.trim(),
        scheduled_day: settingsEls.taskDay.value.trim(),
        cron_expression: settingsEls.taskCron.value.trim(),
        endpoint_id: opt ? opt.dataset.endpointId : '',
        model: opt ? opt.value : '',
        enabled_builtin_tools,
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'create failed');
    }
    settingsEls.taskCreateResult.textContent = 'created';
    settingsEls.taskCreateResult.className = 'settings-hint ok';
    settingsEls.taskName.value = '';
    settingsEls.taskPrompt.value = '';
    await refreshTaskList();
  } catch (e) {
    settingsEls.taskCreateResult.textContent = e.message;
    settingsEls.taskCreateResult.className = 'settings-hint error';
  }
};

// ── email ────────────────────────────────────────────────────────────

let currentEmailAccountId = null;

async function refreshEmailAccountList() {
  try {
    const r = await fetch('/api/email/accounts');
    const { accounts = [] } = await r.json();
    settingsEls.emailAccountList.innerHTML = '';
    if (!accounts.length) {
      settingsEls.emailAccountList.innerHTML = '<li class="settings-hint">none added yet</li>';
      settingsEls.emailInboxCard.hidden = true;
      return;
    }
    for (const a of accounts) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(a.label)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(a.email_address)}</span>` +
        `<button class="btn ghost" data-act="open">open</button>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('[data-act="open"]').onclick = () => openEmailInbox(a.id);
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/email/accounts/' + a.id, { method: 'DELETE' });
        if (currentEmailAccountId === a.id) { currentEmailAccountId = null; settingsEls.emailInboxCard.hidden = true; }
        await refreshEmailAccountList();
      };
      settingsEls.emailAccountList.appendChild(li);
    }
    if (!currentEmailAccountId) openEmailInbox(accounts[0].id);
  } catch (_) {
    settingsEls.emailAccountList.innerHTML = '<li class="settings-hint error">failed to load accounts</li>';
  }
}

settingsEls.addEmailAccountBtn.onclick = async () => {
  const email_address = settingsEls.emailAddress.value.trim();
  const password = settingsEls.emailPassword.value;
  const imap_host = settingsEls.emailImapHost.value.trim();
  if (!email_address || !password || !imap_host) {
    settingsEls.emailAddResult.textContent = 'email, password, and IMAP host are required';
    settingsEls.emailAddResult.className = 'settings-hint error';
    return;
  }
  settingsEls.emailAddResult.textContent = 'connecting…';
  settingsEls.emailAddResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/email/accounts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        label: settingsEls.emailLabel.value.trim(), email_address, password, imap_host,
        imap_port: parseInt(settingsEls.emailImapPort.value || '993', 10),
        smtp_host: settingsEls.emailSmtpHost.value.trim(),
        smtp_port: parseInt(settingsEls.emailSmtpPort.value || '587', 10),
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'add failed');
    }
    settingsEls.emailAddResult.textContent = 'connected';
    settingsEls.emailAddResult.className = 'settings-hint ok';
    settingsEls.emailPassword.value = '';
    await refreshEmailAccountList();
  } catch (e) {
    settingsEls.emailAddResult.textContent = e.message;
    settingsEls.emailAddResult.className = 'settings-hint error';
  }
};

async function openEmailInbox(accountId) {
  currentEmailAccountId = accountId;
  settingsEls.emailInboxCard.hidden = false;
  settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">select a message</p>';
  try {
    const r = await fetch(`/api/email/${accountId}/folders`);
    const { folders = ['INBOX'] } = await r.json();
    settingsEls.emailFolderSelect.innerHTML = '';
    for (const f of folders) {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f;
      settingsEls.emailFolderSelect.appendChild(opt);
    }
    if (folders.includes('INBOX')) settingsEls.emailFolderSelect.value = 'INBOX';
  } catch (_) {
    settingsEls.emailFolderSelect.innerHTML = '<option value="INBOX">INBOX</option>';
  }
  await loadEmailMessages();
}

settingsEls.emailRefreshBtn.onclick = loadEmailMessages;
settingsEls.emailFolderSelect.onchange = loadEmailMessages;

async function loadEmailMessages() {
  if (!currentEmailAccountId) return;
  const folder = settingsEls.emailFolderSelect.value || 'INBOX';
  settingsEls.emailMessageList.innerHTML = '<li class="settings-hint">loading…</li>';
  try {
    const r = await fetch(`/api/email/${currentEmailAccountId}/messages?folder=${encodeURIComponent(folder)}`);
    if (!r.ok) throw new Error('failed to load messages');
    const { messages = [] } = await r.json();
    settingsEls.emailMessageList.innerHTML = '';
    if (!messages.length) {
      settingsEls.emailMessageList.innerHTML = '<li class="settings-hint">empty</li>';
      return;
    }
    for (const m of messages) {
      const li = document.createElement('li');
      li.className = 'endpoint-row' + (m.unread ? ' unread' : '');
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(m.subject || '(no subject)')}</span>` +
        `<span class="endpoint-meta">${escapeHtml(m.from || '')}</span>`;
      li.onclick = () => openEmailMessage(folder, m.uid);
      settingsEls.emailMessageList.appendChild(li);
    }
  } catch (e) {
    settingsEls.emailMessageList.innerHTML = `<li class="settings-hint error">${escapeHtml(e.message)}</li>`;
  }
}

async function openEmailMessage(folder, uid) {
  settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">loading…</p>';
  try {
    const r = await fetch(`/api/email/${currentEmailAccountId}/message/${uid}?folder=${encodeURIComponent(folder)}`);
    if (!r.ok) throw new Error('failed to load message');
    const msg = await r.json();
    settingsEls.emailReadingPane.innerHTML =
      `<div class="email-subject">${escapeHtml(msg.subject || '(no subject)')}</div>` +
      `<div class="email-meta">from ${escapeHtml(msg.from || '')} · ${escapeHtml(msg.date || '')}</div>` +
      `<div class="email-actions">` +
        `<button class="btn ghost" data-act="summarize">summarize</button>` +
        `<button class="btn ghost" data-act="urgency">check urgency</button>` +
        `<button class="btn ghost" data-act="archive">archive</button>` +
        `<button class="btn ghost" data-act="delete">delete</button>` +
      `</div>` +
      `<div class="email-body">${escapeHtml(msg.body)}</div>` +
      `<textarea id="emailReplyBox" placeholder="write a reply…"></textarea>` +
      `<div class="email-actions">` +
        `<button class="btn ghost" data-act="draft">AI draft reply</button>` +
        `<button class="btn send" data-act="reply">send reply</button>` +
      `</div>` +
      `<div class="email-ai-result" id="emailAiResult" hidden></div>`;

    const aiResult = document.getElementById('emailAiResult');
    const showAi = text => { aiResult.hidden = false; aiResult.textContent = text; };

    settingsEls.emailReadingPane.querySelector('[data-act="summarize"]').onclick = () => runEmailAi('summarize', folder, uid, showAi);
    settingsEls.emailReadingPane.querySelector('[data-act="urgency"]').onclick = () => runEmailAi('check_urgency', folder, uid, showAi);
    settingsEls.emailReadingPane.querySelector('[data-act="draft"]').onclick = async () => {
      await runEmailAi('draft_reply', folder, uid, text => { document.getElementById('emailReplyBox').value = text; });
    };
    settingsEls.emailReadingPane.querySelector('[data-act="archive"]').onclick = async () => {
      await fetch(`/api/email/${currentEmailAccountId}/message/${uid}/archive?folder=${encodeURIComponent(folder)}`, { method: 'POST' });
      await loadEmailMessages();
      settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">select a message</p>';
    };
    settingsEls.emailReadingPane.querySelector('[data-act="delete"]').onclick = async () => {
      await fetch(`/api/email/${currentEmailAccountId}/message/${uid}?folder=${encodeURIComponent(folder)}`, { method: 'DELETE' });
      await loadEmailMessages();
      settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">select a message</p>';
    };
    settingsEls.emailReadingPane.querySelector('[data-act="reply"]').onclick = async () => {
      const body = document.getElementById('emailReplyBox').value.trim();
      if (!body) return;
      await fetch(`/api/email/${currentEmailAccountId}/message/${uid}/reply?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body }),
      });
      showAi('reply sent');
    };
  } catch (e) {
    settingsEls.emailReadingPane.innerHTML = `<p class="settings-hint error">${escapeHtml(e.message)}</p>`;
  }
}

async function runEmailAi(action, folder, uid, onResult) {
  const model = currentModel();
  if (!model) { onResult('select a model in the header first'); return; }
  onResult('thinking…');
  try {
    const r = await fetch(`/api/email/${currentEmailAccountId}/message/${uid}/ai?action=${action}&folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'AI action failed');
    }
    const { result } = await r.json();
    onResult(result);
  } catch (e) {
    onResult('error: ' + e.message);
  }
}

// escapeHtml is already defined globally by streamingRenderer.js (loaded
// before this script) — reused here rather than duplicated.

// ── notes (to-do list / checklists) ─────────────────────────────────

let pendingNoteItems = [];

function renderPendingNoteItems() {
  settingsEls.noteItemsEditor.innerHTML = '';
  pendingNoteItems.forEach((item, idx) => {
    const li = document.createElement('li');
    li.className = 'note-item-row';
    li.innerHTML = `<span class="note-item-text">${escapeHtml(item.text)}</span><button class="endpoint-remove" title="Remove">×</button>`;
    li.querySelector('.endpoint-remove').onclick = () => {
      pendingNoteItems.splice(idx, 1);
      renderPendingNoteItems();
    };
    settingsEls.noteItemsEditor.appendChild(li);
  });
}

settingsEls.noteType.addEventListener('change', () => {
  const isChecklist = settingsEls.noteType.value === 'checklist';
  settingsEls.noteContentRow.hidden = isChecklist;
  settingsEls.noteItemsRow.hidden = !isChecklist;
});

settingsEls.noteAddItemBtn.onclick = () => {
  const text = settingsEls.noteNewItem.value.trim();
  if (!text) return;
  pendingNoteItems.push({ text, done: false });
  settingsEls.noteNewItem.value = '';
  renderPendingNoteItems();
};

settingsEls.createNoteBtn.onclick = async () => {
  const title = settingsEls.noteTitle.value.trim();
  if (!title) return;
  try {
    const r = await fetch('/api/notes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title, content: settingsEls.noteContent.value.trim(),
        items: pendingNoteItems, note_type: settingsEls.noteType.value,
        due_date: settingsEls.noteDueDate.value.trim(), repeat: settingsEls.noteRepeat.value,
        label: settingsEls.noteLabel.value.trim(), color: settingsEls.noteColor.value.trim(),
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'create failed');
    }
    settingsEls.noteCreateResult.textContent = 'added';
    settingsEls.noteCreateResult.className = 'settings-hint ok';
    settingsEls.noteTitle.value = '';
    settingsEls.noteContent.value = '';
    settingsEls.noteDueDate.value = '';
    settingsEls.noteLabel.value = '';
    settingsEls.noteColor.value = '';
    pendingNoteItems = [];
    renderPendingNoteItems();
    await refreshNoteList();
  } catch (e) {
    settingsEls.noteCreateResult.textContent = e.message;
    settingsEls.noteCreateResult.className = 'settings-hint error';
  }
};

settingsEls.showArchivedNotes.addEventListener('change', refreshNoteList);

function noteDueBadge(dueDate) {
  if (!dueDate) return '';
  const due = new Date(dueDate);
  if (isNaN(due)) return '';
  const now = new Date();
  const todayStr = now.toDateString();
  let cls = '';
  if (due < now && due.toDateString() !== todayStr) cls = 'overdue';
  else if (due.toDateString() === todayStr) cls = 'due-today';
  return `<span class="note-badge ${cls}">due ${due.toLocaleDateString()}</span>`;
}

async function refreshNoteList() {
  try {
    const archived = settingsEls.showArchivedNotes.checked;
    const r = await fetch('/api/notes?archived=' + (archived ? 'true' : 'false'));
    const { notes: noteList = [] } = await r.json();
    settingsEls.noteList.innerHTML = '';
    if (!noteList.length) {
      settingsEls.noteList.innerHTML = '<li class="settings-hint">nothing here yet</li>';
      return;
    }
    for (const n of noteList) {
      const li = document.createElement('li');
      li.className = 'endpoint-row' + (n.pinned ? ' pinned' : '');
      li.style.flexDirection = 'column';
      li.style.alignItems = 'stretch';

      const doneCount = n.items.filter(i => i.done).length;
      const progress = n.note_type === 'checklist' ? `<span class="endpoint-meta">${doneCount}/${n.items.length} done</span>` : '';
      const repeatBadge = n.repeat && n.repeat !== 'none' ? `<span class="note-badge">${escapeHtml(n.repeat)}</span>` : '';
      const labelBadge = n.label ? `<span class="note-badge">${escapeHtml(n.label)}</span>` : '';

      const header = document.createElement('div');
      header.className = 'endpoint-row';
      header.style.border = 'none';
      header.style.padding = '0';
      const aiButton = n.note_type === 'note' ? `<button class="btn ghost" data-act="ai-improve">improve w/ AI</button>` : '';
      header.innerHTML =
        `<span class="endpoint-label">${escapeHtml(n.title || '(untitled)')}</span>` +
        progress + noteDueBadge(n.due_date) + repeatBadge + labelBadge +
        aiButton +
        `<button class="btn ghost" data-act="pin">${n.pinned ? 'unpin' : 'pin'}</button>` +
        `<button class="btn ghost" data-act="archive">${n.archived ? 'unarchive' : 'archive'}</button>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.appendChild(header);

      const aiBtn = header.querySelector('[data-act="ai-improve"]');
      if (aiBtn) {
        aiBtn.onclick = async () => {
          const model = currentModel();
          if (!model) { alert('select a model in the header first'); return; }
          aiBtn.textContent = 'improving…';
          aiBtn.disabled = true;
          try {
            const prompt = n.content
              ? `Improve the writing, clarity, and grammar of this note. Keep the same meaning and any lists/formatting. Return ONLY the improved text, nothing else.\n\nTitle: ${n.title}\n\n${n.content}`
              : `Write a short, useful note (2-4 sentences) expanding on this title. Return ONLY the note text, nothing else.\n\nTitle: ${n.title}`;
            const r = await fetch('/api/quick-complete', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id, prompt }),
            });
            if (!r.ok) throw new Error('request failed');
            const { text } = await r.json();
            await fetch(`/api/notes/${n.id}`, {
              method: 'PUT', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ content: text.trim() }),
            });
            await refreshNoteList();
          } catch (e) {
            aiBtn.textContent = 'failed';
            aiBtn.disabled = false;
            setTimeout(() => { aiBtn.textContent = 'improve w/ AI'; }, 1500);
          }
        };
      }

      if (n.content) {
        const contentEl = document.createElement('p');
        contentEl.className = 'settings-hint';
        contentEl.textContent = n.content;
        li.appendChild(contentEl);
      }

      if (n.note_type === 'checklist') {
        const itemsEl = document.createElement('ul');
        itemsEl.className = 'endpoint-list';
        n.items.forEach((item, idx) => {
          const itemLi = document.createElement('li');
          itemLi.className = 'note-item-row' + (item.done ? ' done' : '');
          const checked = item.done ? 'checked' : '';
          itemLi.innerHTML = `<input type="checkbox" ${checked} /><span class="note-item-text">${escapeHtml(item.text)}</span>`;
          itemLi.querySelector('input').onchange = async () => {
            await fetch(`/api/notes/${n.id}/items/${idx}/toggle`, { method: 'POST' });
            await refreshNoteList();
          };
          itemsEl.appendChild(itemLi);
        });
        li.appendChild(itemsEl);
      }

      header.querySelector('[data-act="pin"]').onclick = async () => {
        await fetch(`/api/notes/${n.id}/pin`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pinned: !n.pinned }),
        });
        await refreshNoteList();
      };
      header.querySelector('[data-act="archive"]').onclick = async () => {
        await fetch(`/api/notes/${n.id}/archive`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ archived: !n.archived }),
        });
        await refreshNoteList();
      };
      header.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/notes/' + n.id, { method: 'DELETE' });
        await refreshNoteList();
      };
      settingsEls.noteList.appendChild(li);
    }
  } catch (_) {
    settingsEls.noteList.innerHTML = '<li class="settings-hint error">failed to load notes</li>';
  }
}

// Recurring due-date advancement is client-side only, matching odysseus's
// own real behavior (its backend never rewrites due_date either — only a
// browser tab polling notes.js does). Runs on a timer while the Notes tab
// (i.e. the settings modal) is open, same trigger condition odysseus uses.
function advanceRecurringDate(dateStr, repeat) {
  const d = new Date(dateStr);
  if (isNaN(d)) return null;
  if (repeat === 'daily') d.setDate(d.getDate() + 1);
  else if (repeat === 'weekly') d.setDate(d.getDate() + 7);
  else if (repeat === 'monthly') d.setMonth(d.getMonth() + 1);
  else if (repeat === 'yearly') d.setFullYear(d.getFullYear() + 1);
  else return null;
  return d.toISOString();
}

let noteReminderTimer = null;

function startNoteReminderTimer() {
  if (noteReminderTimer) return;
  noteReminderTimer = setInterval(checkRecurringNotes, 30000);
}

function stopNoteReminderTimer() {
  if (noteReminderTimer) { clearInterval(noteReminderTimer); noteReminderTimer = null; }
}

async function checkRecurringNotes() {
  try {
    const r = await fetch('/api/notes?archived=false');
    const { notes: noteList = [] } = await r.json();
    const now = new Date();
    let advanced = false;
    for (const n of noteList) {
      if (!n.due_date || n.repeat === 'none') continue;
      const due = new Date(n.due_date);
      if (isNaN(due) || due >= now) continue;
      const next = advanceRecurringDate(n.due_date, n.repeat);
      if (!next) continue;
      await fetch(`/api/notes/${n.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ due_date: next }),
      });
      advanced = true;
    }
    if (advanced) await refreshNoteList();
  } catch (_) {}
}

// ── presets ──────────────────────────────────────────────────────────

async function refreshPresetList() {
  try {
    const r = await fetch('/api/presets');
    const { presets = [] } = await r.json();
    settingsEls.presetList.innerHTML = '';
    if (!presets.length) {
      settingsEls.presetList.innerHTML = '<li class="settings-hint">no presets yet</li>';
      return;
    }
    for (const p of presets) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(p.name)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(p.model || 'no model set')}</span>` +
        `<button class="btn ghost" data-act="apply">apply</button>` +
        `<button class="endpoint-remove" title="Remove">×</button>`;
      li.querySelector('[data-act="apply"]').onclick = () => applyPreset(p);
      li.querySelector('.endpoint-remove').onclick = async () => {
        await fetch('/api/presets/' + p.id, { method: 'DELETE' });
        await refreshPresetList();
      };
      settingsEls.presetList.appendChild(li);
    }
  } catch (_) {
    settingsEls.presetList.innerHTML = '<li class="settings-hint error">failed to load presets</li>';
  }
}

settingsEls.savePresetBtn.onclick = async () => {
  const name = settingsEls.presetName.value.trim();
  if (!name) return;
  const model = currentModel();
  try {
    const r = await fetch('/api/presets', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, system_prompt: els.sysPrompt.value.trim(),
        endpoint_id: model ? model.endpoint_id : '', model: model ? model.id : '',
        enabled_mcp_servers: [...enabledMcpServers], enabled_builtin_tools: [...enabledBuiltinTools],
      }),
    });
    if (!r.ok) throw new Error('save failed');
    settingsEls.presetSaveResult.textContent = 'saved';
    settingsEls.presetSaveResult.className = 'settings-hint ok';
    settingsEls.presetName.value = '';
    await refreshPresetList();
  } catch (e) {
    settingsEls.presetSaveResult.textContent = e.message;
    settingsEls.presetSaveResult.className = 'settings-hint error';
  }
};

function applyPreset(p) {
  els.sysPrompt.value = p.system_prompt || '';
  try { localStorage.setItem('sf_sys', els.sysPrompt.value); } catch (_) {}
  if (p.model) {
    const opt = [...els.modelList.options].find(o => o.value === p.model && o.dataset.endpointId === p.endpoint_id);
    if (opt) els.modelList.value = opt.value;
  }
  enabledMcpServers = new Set(p.enabled_mcp_servers || []);
  enabledBuiltinTools = new Set(p.enabled_builtin_tools || []);
  saveEnabledMcpServers();
  saveEnabledBuiltinTools();
  // Re-sync every tool checkbox's visible state to the newly-applied set —
  // simplest way given each toggle owns its own checked-state elsewhere is
  // to just re-run the same assignments openSettings() would do on load.
  settingsEls.toggleManageMemory.checked = enabledBuiltinTools.has('manage_memory');
  settingsEls.toggleSearchDocuments.checked = enabledBuiltinTools.has('search_documents');
  settingsEls.toggleManageTasks.checked = enabledBuiltinTools.has('manage_tasks');
  settingsEls.toggleManageEmail.checked = enabledBuiltinTools.has('manage_email');
  settingsEls.toggleManageNotes.checked = enabledBuiltinTools.has('manage_notes');
  settingsEls.toggleSearchWeb.checked = enabledBuiltinTools.has('search_web');
  settingsEls.toggleGithubCli.checked = enabledBuiltinTools.has('github_cli');
  settingsEls.toggleDeepResearch.checked = enabledBuiltinTools.has('deep_research');
  settingsEls.toggleEditFile.checked = enabledBuiltinTools.has('edit_file');
  settingsEls.editApprovalRow.hidden = !settingsEls.toggleEditFile.checked;
  settingsEls.toggleRunShell.checked = enabledBuiltinTools.has('run_shell');
  refreshMcpServerList();
}

// ── usage / cost ─────────────────────────────────────────────────────

async function refreshUsage() {
  try {
    const r = await fetch('/api/usage');
    const data = await r.json();
    const fmtCost = c => c ? '$' + c.toFixed(4) : '$0';
    let html = `<div class="endpoint-row"><span class="endpoint-label">Today</span>` +
      `<span class="endpoint-meta">${data.today.turns} turns · ${data.today.input_tokens + data.today.output_tokens} tokens · ${fmtCost(data.today.cost)}</span></div>` +
      `<div class="endpoint-row"><span class="endpoint-label">All time</span>` +
      `<span class="endpoint-meta">${data.all_time.turns} turns · ${data.all_time.input_tokens + data.all_time.output_tokens} tokens · ${fmtCost(data.all_time.cost)}</span></div>`;
    const models = Object.entries(data.by_model || {});
    if (models.length) {
      html += '<div class="settings-hint" style="margin-top:8px">By model:</div>';
      for (const [name, t] of models) {
        html += `<div class="endpoint-row"><span class="endpoint-label">${name}</span>` +
          `<span class="endpoint-meta">${t.turns} turns · ${fmtCost(t.cost)}</span></div>`;
      }
    }
    settingsEls.usageSummary.innerHTML = html;
  } catch (_) {
    settingsEls.usageSummary.innerHTML = '<p class="settings-hint error">failed to load usage</p>';
  }
}

// ── hardware-aware model suggestions ────────────────────────────────

async function refreshHardware() {
  try {
    const r = await fetch('/api/hardware');
    const data = await r.json();
    const memLine = data.vram_gb
      ? `${data.vram_gb} GB VRAM detected (GPU)`
      : data.ram_gb ? `${data.ram_gb} GB system RAM (no GPU detected — CPU/unified-memory sizing)` : 'could not detect memory';
    settingsEls.hardwareInfo.innerHTML =
      `<p class="settings-hint">${memLine}, ~${data.budget_gb} GB usable budget for a model.</p>`;
    settingsEls.hardwareModelList.innerHTML = '';
    for (const c of data.all_candidates || []) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${c.fits ? '✓' : '✗'} ${c.name}</span>` +
        `<span class="endpoint-meta">~${c.size_gb} GB${c.fits ? '' : ' — likely too large'}</span>` +
        `<button class="btn ghost" data-act="pull">pull</button>`;
      const statusEl = li.querySelector('.endpoint-meta');
      li.querySelector('[data-act="pull"]').onclick = e => pullModel(c.name, e.target, statusEl);
      settingsEls.hardwareModelList.appendChild(li);
    }
  } catch (_) {
    settingsEls.hardwareInfo.innerHTML = '<p class="settings-hint error">failed to detect hardware</p>';
  }
}

// ── backup / restore ─────────────────────────────────────────────────

settingsEls.downloadBackupBtn.onclick = () => {
  window.location.href = '/api/backup';
};

settingsEls.restoreBackupBtn.onclick = async () => {
  const file = settingsEls.restoreFile.files[0];
  if (!file) return;
  if (!confirm('This overwrites your current data with the contents of this backup. This cannot be undone. Continue?')) return;
  settingsEls.restoreResult.textContent = 'restoring…';
  settingsEls.restoreResult.className = 'settings-hint';
  try {
    const formData = new FormData();
    formData.append('file', file);
    const r = await fetch('/api/restore', { method: 'POST', body: formData });
    if (!r.ok) throw new Error('restore failed');
    settingsEls.restoreResult.textContent = 'restored — reloading…';
    settingsEls.restoreResult.className = 'settings-hint ok';
    setTimeout(() => window.location.reload(), 1200);
  } catch (e) {
    settingsEls.restoreResult.textContent = e.message;
    settingsEls.restoreResult.className = 'settings-hint error';
  }
};

// ── compare mode ─────────────────────────────────────────────────────

const compareEls = {
  modal: $('compareModal'), closeBtn: $('compareCloseBtn'), form: $('compareForm'),
  input: $('comparePromptInput'), columns: $('compareColumns'), addColumnBtn: $('compareAddColumnBtn'),
};

function compareModelOptionsHtml() {
  return models.map(m =>
    `<option value="${escapeHtml(m.id)}" data-endpoint-id="${escapeHtml(m.endpoint_id)}">${escapeHtml(m.id)} (${escapeHtml(window.providerLabel ? window.providerLabel(m.provider) : m.provider)})</option>`
  ).join('');
}

function addCompareColumn() {
  const col = document.createElement('div');
  col.className = 'compare-column';
  col.innerHTML =
    `<div class="compare-column-header">` +
      `<select class="compare-model-select">${compareModelOptionsHtml()}</select>` +
      `<button class="endpoint-remove" title="Remove column">×</button>` +
    `</div>` +
    `<div class="compare-column-output"></div>`;
  col.querySelector('.endpoint-remove').onclick = () => {
    if (compareEls.columns.children.length > 1) col.remove();
  };
  compareEls.columns.appendChild(col);
}

function openCompare() {
  compareEls.modal.hidden = false;
  if (!compareEls.columns.children.length) {
    addCompareColumn();
    addCompareColumn();
  }
}
function closeCompare() { compareEls.modal.hidden = true; }

els.compareBtn = $('compareBtn');
els.compareBtn.onclick = openCompare;
compareEls.closeBtn.onclick = closeCompare;
compareEls.addColumnBtn.onclick = addCompareColumn;
compareEls.modal.addEventListener('click', e => { if (e.target === compareEls.modal) closeCompare(); });

async function runCompareColumn(col, prompt) {
  const select = col.querySelector('.compare-model-select');
  const output = col.querySelector('.compare-column-output');
  const opt = select.selectedOptions[0];
  if (!opt) { output.textContent = 'no model selected'; return; }
  output.textContent = '';
  try {
    const r = await fetch('/api/chat_stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        endpoint_id: opt.dataset.endpointId, model: opt.value,
        messages: [{ role: 'user', content: prompt }],
      }),
    });
    if (!r.ok) { output.textContent = 'error: server ' + r.status; return; }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const obj = JSON.parse(line.slice(5).trim());
          if (obj.delta) output.textContent += obj.delta;
          else if (obj.error) output.textContent += '\n[error: ' + obj.error + ']';
        } catch (_) {}
      }
    }
  } catch (e) {
    output.textContent = 'error: ' + e.message;
  }
}

compareEls.form.addEventListener('submit', e => {
  e.preventDefault();
  const prompt = compareEls.input.value.trim();
  if (!prompt) return;
  for (const col of compareEls.columns.children) runCompareColumn(col, prompt);
});

// ── command palette (Ctrl+K / Cmd+K) ────────────────────────────────

const paletteEls = { overlay: $('paletteOverlay'), input: $('paletteInput'), list: $('paletteList') };
let paletteActions = [];
let paletteActiveIndex = 0;

function buildPaletteActions() {
  paletteActions = [
    { label: 'New chat', run: clearChat },
    { label: 'Export chat as markdown', run: exportChat },
    { label: 'Open settings', run: openSettings },
    { label: 'Compare models', run: openCompare },
    { label: 'Toggle light/dark theme', run: () => settingsEls.lightModeToggle.click() },
  ];
  for (const tab of document.querySelectorAll('.settings-tab')) {
    paletteActions.push({
      label: 'Settings → ' + tab.textContent,
      run: () => { openSettings(); tab.click(); },
    });
  }
}

function renderPalette(filter) {
  const q = filter.trim().toLowerCase();
  const matches = paletteActions.filter(a => a.label.toLowerCase().includes(q));
  paletteEls.list.innerHTML = '';
  paletteActiveIndex = 0;
  matches.forEach((action, idx) => {
    const li = document.createElement('li');
    li.textContent = action.label;
    if (idx === 0) li.classList.add('active');
    li.onclick = () => runPaletteAction(action);
    paletteEls.list.appendChild(li);
  });
  return matches;
}

let paletteMatches = [];

function openPalette() {
  if (!paletteActions.length) buildPaletteActions();
  paletteEls.overlay.hidden = false;
  paletteEls.input.value = '';
  paletteMatches = renderPalette('');
  paletteEls.input.focus();
}
function closePalette() { paletteEls.overlay.hidden = true; }

function runPaletteAction(action) {
  closePalette();
  action.run();
}

paletteEls.input.addEventListener('input', () => { paletteMatches = renderPalette(paletteEls.input.value); });
paletteEls.overlay.addEventListener('click', e => { if (e.target === paletteEls.overlay) closePalette(); });
paletteEls.input.addEventListener('keydown', e => {
  const items = [...paletteEls.list.children];
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    paletteActiveIndex = Math.min(paletteActiveIndex + 1, items.length - 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    paletteActiveIndex = Math.max(paletteActiveIndex - 1, 0);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (paletteMatches[paletteActiveIndex]) runPaletteAction(paletteMatches[paletteActiveIndex]);
    return;
  } else if (e.key === 'Escape') {
    closePalette();
    return;
  } else {
    return;
  }
  items.forEach((li, idx) => li.classList.toggle('active', idx === paletteActiveIndex));
});

document.addEventListener('keydown', e => {
  const key = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && key === 'k') {
    e.preventDefault();
    if (paletteEls.overlay.hidden) openPalette(); else closePalette();
  } else if (key === 'escape' && !paletteEls.overlay.hidden) {
    closePalette();
  }
});

// ── voice input ──────────────────────────────────────────────────────
//
// Browser-native Speech Recognition (Chrome/Edge; not universally
// supported — feature-detected, and the mic button stays hidden if it
// isn't available rather than showing a button that would just error).

const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SpeechRecognitionCtor && els.micBtn) {
  const recognition = new SpeechRecognitionCtor();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-US';
  let recognizing = false;

  recognition.onresult = e => {
    const transcript = e.results[0][0].transcript;
    els.input.value += (els.input.value && !els.input.value.endsWith(' ') ? ' ' : '') + transcript;
    els.input.dispatchEvent(new Event('input'));
  };
  recognition.onend = () => { recognizing = false; els.micBtn.classList.remove('recording'); };
  recognition.onerror = () => { recognizing = false; els.micBtn.classList.remove('recording'); };

  els.micBtn.hidden = false;
  els.micBtn.onclick = () => {
    if (recognizing) { recognition.stop(); return; }
    recognizing = true;
    els.micBtn.classList.add('recording');
    try { recognition.start(); } catch (_) { recognizing = false; els.micBtn.classList.remove('recording'); }
  };
}

// ── hardware tab: one-click model pull ──────────────────────────────

async function pullModel(name, btn, statusEl) {
  btn.disabled = true;
  statusEl.textContent = 'starting…';
  try {
    const r = await fetch('/api/hardware/pull', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: name }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || 'failed to start pull');
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let sawError = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const obj = JSON.parse(line.slice(5).trim());
          if (obj.error) { sawError = obj.error; }
          else if (obj.status) {
            let text = obj.status;
            if (obj.total && obj.completed) text += ' ' + Math.round(100 * obj.completed / obj.total) + '%';
            statusEl.textContent = text;
          }
        } catch (_) {}
      }
    }
    if (sawError) throw new Error(sawError);
    statusEl.textContent = 'done';
    btn.textContent = 'pulled';
  } catch (e) {
    statusEl.textContent = 'error: ' + e.message;
    btn.disabled = false;
  }
}

// ── chats tab: search + pin ──────────────────────────────────────────

if (settingsEls.chatSearchInput) {
  let searchDebounce = null;
  settingsEls.chatSearchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => refreshSessionList(), 250);
  });
}

// ── auto-titling a newly saved chat via the model itself ────────────

async function generateSessionTitle(sessionId, model) {
  try {
    const convo = messages.slice(0, 2).map(m => `${m.role}: ${m.content}`).join('\n').slice(0, 2000);
    const r = await fetch('/api/quick-complete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        endpoint_id: model.endpoint_id, model: model.id,
        prompt: `Give this conversation a short title (3-6 words, no quotes, no trailing punctuation):\n\n${convo}`,
      }),
    });
    if (!r.ok) return;
    const { text } = await r.json();
    const title = (text || '').trim().replace(/^["']|["']$/g, '').replace(/[.!?]+$/, '').slice(0, 60);
    if (!title) return;
    await fetch('/api/chat/sessions/' + sessionId, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (sessionId === currentSessionId && !settingsEls.modal.hidden) refreshSessionList();
  } catch (_) {
    // Best-effort — the session already has the truncated-first-message
    // fallback title, so a failed rename here is cosmetic, not data loss.
  }
}

// ── theme ────────────────────────────────────────────────────────────

function applyTheme(light) {
  document.documentElement.classList.toggle('light', light);
}
let storedLight = false;
try { storedLight = localStorage.getItem('sf_light') === '1'; } catch (_) {}
applyTheme(storedLight);
settingsEls.lightModeToggle.checked = storedLight;
settingsEls.lightModeToggle.addEventListener('change', () => {
  applyTheme(settingsEls.lightModeToggle.checked);
  try { localStorage.setItem('sf_light', settingsEls.lightModeToggle.checked ? '1' : '0'); } catch (_) {}
});

// restoreSession() rebuilds regenerate buttons' model dropdowns from the
// `models` array, so it must run after loadModels() has actually populated
// it — chained rather than fired in parallel, or a page-load restore would
// build those dropdowns empty (loadModels()'s fetch hadn't resolved yet).
loadModels().then(restoreSession);
