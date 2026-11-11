'use strict';

const COPY_FEEDBACK_MS = 1200;
const CONTROLS_SAVE_DEBOUNCE_MS = 600;
const NEAR_BOTTOM_PX = 40;

function icon(name, extraClass = '') {
  return `<svg class="icon${extraClass ? ' ' + extraClass : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function showAccount(user) {
  const name = user.name || user.email;
  document.getElementById('accountName').textContent = name;
  document.getElementById('accountSub').textContent = user.role === 'admin' ? `${user.email} · admin` : user.email;
  document.getElementById('accountAvatar').textContent = name.trim().charAt(0) || 's';
  document.getElementById('passwordFormUser').value = user.email;
}

const _rawFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const method = (init.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    init = { ...init, headers: { ...(init.headers || {}), 'X-Starfire-Client': '1' } };
  }
  const response = await _rawFetch(input, init);
  const path = new URL(typeof input === 'string' ? input : input.url, window.location.href).pathname;
  if (response.status === 401 && path.startsWith('/api/') && !path.startsWith('/api/auth/')) {
    window.location.replace('/login.html');
  }
  return response;
};

let currentUser = null;

function applyUserScope(user) {
  currentUser = user;
  document.body.classList.toggle('is-admin', user.role === 'admin');
  let previous = null;
  try { previous = localStorage.getItem('sf_user'); } catch (_) {}
  if (previous !== user.id) {
    try {
      localStorage.removeItem('sf_current_session');
      localStorage.removeItem('sf_draft_messages');
      localStorage.setItem('sf_user', user.id);
    } catch (_) {}
    if (typeof currentSessionId !== 'undefined') currentSessionId = null;
  }
}

async function setUpLogout() {
  const button = document.getElementById('logoutBtn');
  const response = await fetch('/api/auth/me');
  if (!response.ok) return;
  const user = await response.json();
  applyUserScope(user);
  showAccount(user);
  button.title = `Logged in as ${user.email}`;
  button.hidden = false;
  button.addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.replace('/login.html');
  });
  setUpPasswordChange(user);
}

function setUpPasswordChange(user) {
  const card = document.getElementById('accountCard');
  const form = document.getElementById('passwordForm');
  const status = document.getElementById('passwordStatus');
  const submit = document.getElementById('passwordSubmit');
  document.getElementById('accountWho').textContent = `Logged in as ${user.email} (${user.role})`;
  card.hidden = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = {
      current: document.getElementById('currentPassword').value,
      password: document.getElementById('newPassword').value,
      confirm: document.getElementById('confirmPassword').value,
    };
    status.className = 'settings-hint';
    status.textContent = 'saving…';
    submit.disabled = true;
    try {
      const response = await fetch('/api/auth/password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `failed (${response.status})`);
      form.reset();
      status.className = 'settings-hint ok';
      status.textContent = 'password changed - other browsers have been logged out';
      showToast('Password changed');
    } catch (error) {
      status.className = 'settings-hint error';
      status.textContent = `couldn't change it: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });
}

const $ = id => document.getElementById(id);

const els = {
  output:      $('output'),
  form:        $('form'),
  input:       $('msgInput'),
  sendBtn:     $('sendBtn'),
  stopBtn:     $('stopBtn'),
  micBtn:      $('micBtn'),
  capabilityWarning: $('capabilityWarning'),
  modelList:   $('modelList'),
  exportBtn:   $('exportBtn'),
  clearBtn:    $('clearBtn'),
  settingsBtn: $('settingsBtn'),
  dot:         $('dot'),
  statusText:  $('statusText'),
  sysPrompt:   $('sysPrompt'),
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
  filesystemPath: $('filesystemPath'),
  browseDirBtn: $('browseDirBtn'),
  dirBrowserBox: $('dirBrowserBox'),
  dirBrowserPath: $('dirBrowserPath'),
  dirBrowserList: $('dirBrowserList'),
  dirBrowserSelectBtn: $('dirBrowserSelectBtn'),
  addFilesystemBtn: $('addFilesystemBtn'),
  mcpRepoSelect: $('mcpRepoSelect'),
  mcpRepoConfigureBox: $('mcpRepoConfigureBox'),
  mcpRepoPathRow: $('mcpRepoPathRow'),
  mcpRepoPath: $('mcpRepoPath'),
  mcpRepoEnvFields: $('mcpRepoEnvFields'),
  mcpRepoAddBtn: $('mcpRepoAddBtn'),
  mcpRepoResult: $('mcpRepoResult'),
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
  ruleAccountSelect: $('ruleAccountSelect'),
  ruleFolder:      $('ruleFolder'),
  ruleMatchField:  $('ruleMatchField'),
  ruleMatchValue:  $('ruleMatchValue'),
  ruleAction:      $('ruleAction'),
  ruleModelRow:    $('ruleModelRow'),
  ruleModelSelect: $('ruleModelSelect'),
  createRuleBtn:   $('createRuleBtn'),
  ruleCreateResult: $('ruleCreateResult'),
  ruleList:        $('ruleList'),
  emailFolderSelect: $('emailFolderSelect'),
  emailRefreshBtn: $('emailRefreshBtn'),
  emailMessageList: $('emailMessageList'),
  emailReadingPane: $('emailReadingPane'),
  toggleManageNotes: $('toggleManageNotes'),
  toggleSearchWeb: $('toggleSearchWeb'),
  toggleGithubCli: $('toggleGithubCli'),
  toggleDeepResearch: $('toggleDeepResearch'),
  toggleGenerateImage: $('toggleGenerateImage'),
  toggleGenerateAudio: $('toggleGenerateAudio'),
  toggleGenerateDocument: $('toggleGenerateDocument'),
  toggleEditFile: $('toggleEditFile'),
  editApprovalRow: $('editApprovalRow'),
  toggleEditApproval: $('toggleEditApproval'),
  toggleRunShell: $('toggleRunShell'),
  shellApprovalRow: $('shellApprovalRow'),
  toggleShellApproval: $('toggleShellApproval'),
  toggleFindImage: $('toggleFindImage'),
  mcpTransport: $('mcpTransport'),
  mcpCommandRow: $('mcpCommandRow'),
  mcpArgsRow: $('mcpArgsRow'),
  mcpUrlRow: $('mcpUrlRow'),
  mcpAuthRow: $('mcpAuthRow'),
  mcpUrl: $('mcpUrl'),
  mcpAuthHeader: $('mcpAuthHeader'),
  presetName: $('presetName'),
  savePresetBtn: $('savePresetBtn'),
  presetSaveResult: $('presetSaveResult'),
  presetList: $('presetList'),
  usageSummary: $('usageSummary'),
  hardwareInfo: $('hardwareInfo'),
  hardwareModelList: $('hardwareModelList'),
  ollamaStatus: $('ollamaStatus'),
  downloadBackupBtn: $('downloadBackupBtn'),
  toggleGenerateVideo: $('toggleGenerateVideo'),
  comfyBaseUrl: $('comfyBaseUrl'),
  comfyDetectBtn: $('comfyDetectBtn'),
  comfyStatus: $('comfyStatus'),
  comfyCheckpointsDir: $('comfyCheckpointsDir'),
  comfyBrowseBtn: $('comfyBrowseBtn'),
  comfyDirBrowserBox: $('comfyDirBrowserBox'),
  comfyDirBrowserPath: $('comfyDirBrowserPath'),
  comfyDirBrowserList: $('comfyDirBrowserList'),
  comfyDirBrowserSelectBtn: $('comfyDirBrowserSelectBtn'),
  comfyCheckpointSelect: $('comfyCheckpointSelect'),
  comfyNegativePrompt: $('comfyNegativePrompt'),
  comfySaveBtn: $('comfySaveBtn'),
  comfySaveResult: $('comfySaveResult'),
  comfyPullUrl: $('comfyPullUrl'),
  comfyPullFilename: $('comfyPullFilename'),
  comfyPullBtn: $('comfyPullBtn'),
  comfyPullProgress: $('comfyPullProgress'),
  comfyPullStatus: $('comfyPullStatus'),
  piperVoicePath: $('piperVoicePath'),
  piperSaveBtn: $('piperSaveBtn'),
  piperSaveResult: $('piperSaveResult'),
  piperVoiceName: $('piperVoiceName'),
  piperVoiceQuality: $('piperVoiceQuality'),
  piperVoicesDirForPull: $('piperVoicesDirForPull'),
  piperPullBtn: $('piperPullBtn'),
  piperPullProgress: $('piperPullProgress'),
  piperPullStatus: $('piperPullStatus'),
  workflowName: $('workflowName'),
  workflowJson: $('workflowJson'),
  workflowPromptNode: $('workflowPromptNode'),
  workflowPromptKey: $('workflowPromptKey'),
  saveWorkflowBtn: $('saveWorkflowBtn'),
  workflowSaveResult: $('workflowSaveResult'),
  workflowList: $('workflowList'),
  restoreFile: $('restoreFile'),
  restoreBackupBtn: $('restoreBackupBtn'),
  restoreResult: $('restoreResult'),
  noteTemplateSelect: $('noteTemplateSelect'),
  useTemplateBtn:  $('useTemplateBtn'),
  saveAsTemplateBtn: $('saveAsTemplateBtn'),
  noteTemplateList: $('noteTemplateList'),
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
let models   = [];
let abortController = null;
let currentSessionId = null;
try { currentSessionId = localStorage.getItem('sf_current_session') || null; } catch (_) {}

let enabledMcpServers = new Set();
try { enabledMcpServers = new Set(JSON.parse(localStorage.getItem('sf_mcp_enabled') || '[]')); } catch (_) {}
function saveEnabledMcpServers() {
  try { localStorage.setItem('sf_mcp_enabled', JSON.stringify([...enabledMcpServers])); } catch (_) {}
  checkModelToolCapability();
}

let enabledBuiltinTools = new Set();
try { enabledBuiltinTools = new Set(JSON.parse(localStorage.getItem('sf_builtin_tools_enabled') || '[]')); } catch (_) {}
function saveEnabledBuiltinTools() {
  try { localStorage.setItem('sf_builtin_tools_enabled', JSON.stringify([...enabledBuiltinTools])); } catch (_) {}
  checkModelToolCapability();
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

function extractCodeBlocks(text) {
  const blocks = [];
  const lines = text.split('\n');
  let pos = 0;
  let open = null;
  for (const line of lines) {
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence && !open) {
      open = { marker: fence[1], lang: fence[2].trim().split(/\s+/)[0].toLowerCase(), start: pos, fenceLine: line, codeLines: [] };
    } else if (fence && open && fence[1][0] === open.marker[0] && fence[1].length >= open.marker.length && !fence[2].trim()) {
      blocks.push({ lang: open.lang, code: open.codeLines.join('\n'), start: open.start, end: pos + line.length, fenceLine: open.fenceLine, closeLine: line });
      open = null;
    } else if (open) {
      open.codeLines.push(line);
    }
    pos += line.length + 1;
  }
  return blocks;
}

function replaceCodeBlocks(text, replacements) {
  let out = text;
  for (const { block, code } of [...replacements].sort((a, b) => b.block.start - a.block.start)) {
    out = out.slice(0, block.start) + block.fenceLine + '\n' + code.replace(/\n+$/, '') + '\n' + block.closeLine + out.slice(block.end);
  }
  return out;
}

const CODE_STATUS_LABELS = { checking: '… checking', fixing: '… fixing', ok: '✓ syntax ok', error: '✗ syntax error' };

function setCodeStatus(box, check) {
  const status = box.querySelector('.code-status');
  const btn = box.querySelector('.code-copy-btn');
  if (!status) return;
  const state = check ? check.status : '';
  let label = CODE_STATUS_LABELS[state] || '';
  if (state === 'ok' && check.fixed) label = '✓ fixed · syntax ok';
  if (state === 'error' && check.line) label += ' on line ' + check.line;
  status.textContent = label;
  status.hidden = !label;
  status.className = 'code-status ' + state;
  status.title = check && check.message ? check.message : '';
  box.dataset.check = state;
  if (btn) btn.disabled = state === 'checking' || state === 'fixing';
}

function codeBoxForBlock(container, blocks, index) {
  const pres = [...container.querySelectorAll('pre')];
  const pre = pres.length === blocks.length
    ? pres[index]
    : pres.find(p => (p.textContent || '').trim() === blocks[index].code.trim());
  return pre && pre.parentElement && pre.parentElement.classList.contains('code-box') ? pre.parentElement : null;
}

function decorateCodeBlocks(container, text, checks) {
  const blocks = text ? extractCodeBlocks(text) : [];
  const pres = [...container.querySelectorAll('pre')];
  const aligned = pres.length === blocks.length;
  pres.forEach((pre, i) => {
    const code = pre.querySelector('code');
    let box = pre.parentElement && pre.parentElement.classList.contains('code-box') ? pre.parentElement : null;
    if (!box) {
      const langClass = code ? [...code.classList].find(c => c.startsWith('language-') && c !== 'language-undefined') : '';
      const lang = langClass ? langClass.slice('language-'.length) : '';
      box = document.createElement('div');
      box.className = 'code-box';
      const head = document.createElement('div');
      head.className = 'code-head';
      head.innerHTML = `<span class="code-lang">${escapeHtml(lang || 'code')}</span><span class="code-status" hidden></span>`;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'code-copy-btn';
      btn.innerHTML = icon('copy') + '<span>Copy</span>';
      btn.title = 'Copy this code';
      btn.onclick = () => copyToClipboard(btn, code ? code.textContent : pre.textContent);
      head.appendChild(btn);
      pre.replaceWith(box);
      box.append(head, pre);
    }
    let check = null;
    if (checks && checks.length) {
      const blockIndex = aligned ? i : blocks.findIndex(b => b.code.trim() === (pre.textContent || '').trim());
      check = checks.find(c => c.block === blockIndex) || null;
    }
    setCodeStatus(box, check);
  });
}

async function checkCodeBlock(lang, code) {
  const r = await fetch('/api/code-check', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ language: lang, code }),
  });
  if (!r.ok) throw new Error('code check failed (' + r.status + ')');
  return r.json();
}

async function requestCodeFix(model, block, check) {
  const prompt =
    `This ${block.lang} code block from your last reply fails a ${check.checker || 'syntax'} check:\n\n` +
    `${check.message}\n\n${block.fenceLine}\n${block.code}\n${block.closeLine}\n\n` +
    `Reply with only the corrected ${block.lang} code in one fenced code block and nothing else.`;
  const r = await fetch('/api/quick-complete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id, prompt }),
  });
  if (!r.ok) return null;
  const { text } = await r.json();
  const blocks = extractCodeBlocks(text || '');
  return blocks.length ? blocks[0].code : null;
}

const CODE_FIX_ATTEMPTS = 2;

async function verifyCodeBlocks(container, aiIdx, model) {
  const msg = messages[aiIdx];
  if (!msg || !prefs.checkCode) return;
  const text = msg.content;
  const blocks = extractCodeBlocks(text);
  const todo = blocks.map((block, index) => ({ block, index })).filter(x => x.block.lang);
  if (!todo.length) return;
  for (const { index } of todo) {
    const box = codeBoxForBlock(container, blocks, index);
    if (box) setCodeStatus(box, { status: 'checking' });
  }
  const checks = [];
  const replacements = [];
  for (const { block, index } of todo) {
    const box = codeBoxForBlock(container, blocks, index);
    let current = block;
    let check;
    try {
      check = await checkCodeBlock(block.lang, block.code);
      const firstError = check;
      for (let attempt = 1; check.status === 'error' && attempt <= CODE_FIX_ATTEMPTS && model; attempt++) {
        if (box) setCodeStatus(box, { status: 'fixing', message: `attempt ${attempt} of ${CODE_FIX_ATTEMPTS}: ${check.message}` });
        const fixed = await requestCodeFix(model, current, check);
        if (!fixed || fixed.trim() === current.code.trim()) { check = firstError; break; }
        const again = await checkCodeBlock(block.lang, fixed);
        if (again.status === 'ok') {
          replacements.push({ block, code: fixed });
          check = { ...again, fixed: attempt, message: 'was: ' + firstError.message };
          break;
        }
        current = { ...current, code: fixed };
        check = attempt === CODE_FIX_ATTEMPTS ? firstError : again;
      }
    } catch (e) {
      check = { status: 'skipped', message: e.message };
    }
    checks.push({ block: index, ...check });
    if (box) setCodeStatus(box, check);
  }
  if (messages[aiIdx] !== msg) return;
  msg.code_checks = checks;
  if (replacements.length) {
    msg.content = replaceCodeBlocks(text, replacements);
    if (container.isConnected) renderAiContent(container, msg.content, checks);
  }
  persistSession();
}

function addEditButton(actions, idx) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'line-action-btn';
  labelActionButton(btn, 'edit', 'Edit and resend');
  btn.onclick = () => startEdit(idx);
  actions.appendChild(btn);
}

function addRegenerateButton(actions, idx) {

  const select = document.createElement('select');
  els.output.querySelectorAll('.regen-control').forEach(control => control.remove());
  select.className = 'regen-model-select regen-control';
  select.title = 'Model to regenerate with';
  select.setAttribute('aria-label', 'Model to regenerate with');
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
  btn.classList.add('regen-control');
  labelActionButton(btn, 'regenerate', 'Regenerate with the chosen model');
  btn.onclick = async () => {
    let userIdx = idx - 1;
    while (userIdx >= 0 && messages[userIdx].role !== 'user') userIdx--;
    if (userIdx < 0 || busy) return;
    const text = messages[userIdx].content;
    const opt = select.selectedOptions[0];
    const overrideModel = opt ? { id: opt.value, endpoint_id: opt.dataset.endpointId } : null;
    await forkSession(userIdx);
    rerenderAll();
    send(text, overrideModel);
  };
  actions.appendChild(select);
  actions.appendChild(btn);
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

function diffToHtml(diffText) {
  const escape = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  return diffText.split('\n').map(line => {
    const esc = escape(line);
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) return `<span class="diff-line-meta">${esc}</span>`;
    if (line.startsWith('+')) return `<span class="diff-line-add">${esc}</span>`;
    if (line.startsWith('-')) return `<span class="diff-line-remove">${esc}</span>`;
    return `<span class="diff-line-context">${esc}</span>`;
  }).join('');
}

const openApprovals = new Map();

function trackApproval(pendingId, card) {
  card.dataset.pendingId = pendingId;
  openApprovals.set(pendingId, card);
}

function showOpenApprovals() {
  if (!openApprovals.size) return;
  const w = $('welcome');
  if (w) w.remove();
  openApprovals.forEach(card => els.output.appendChild(card));
  els.output.scrollTop = els.output.scrollHeight;
}

async function restorePendingApprovals() {
  try {
    const [shell, edits] = await Promise.all(['/api/pending-shell', '/api/pending-edits'].map(async url => {
      const r = await fetch(url);
      return r.ok ? r.json() : {};
    }));
    for (const card of pendingApprovalCards(shell.commands, edits.edits)) {
      if (openApprovals.has(card.id)) continue;
      if (card.kind === 'shell') renderShellApprovalCard(card.data);
      else renderDiffCard(JSON.stringify(card.data));
    }
  } catch (e) {
    console.warn('starfire.ai: could not load pending approvals', e);
  }
}

function renderDiffCard(resultJson) {
  let data;
  try { data = JSON.parse(resultJson); } catch (_) { printLine('» edit_file done', 'info'); return; }
  const w = $('welcome');
  if (w) w.remove();

  const card = document.createElement('div');
  card.className = 'diff-card';
  const status = data.staged ? 'proposed edit - awaiting approval' : data.applied ? 'applied edit' : 'edit failed';
  card.innerHTML =
    `<div class="diff-card-header">${status} - ${escapeHtml(data.path || '')}${data.error ? ' - ' + escapeHtml(data.error) : ''}</div>` +
    `<pre>${diffToHtml(data.diff || '')}</pre>`;

  if (data.staged && data.pending_id) {
    trackApproval(data.pending_id, card);
    const actionsRow = document.createElement('div');
    actionsRow.className = 'diff-card-actions';
    const approveBtn = document.createElement('button');
    approveBtn.type = 'button'; approveBtn.className = 'btn send'; approveBtn.textContent = 'approve';
    const rejectBtn = document.createElement('button');
    rejectBtn.type = 'button'; rejectBtn.className = 'btn ghost'; rejectBtn.textContent = 'reject';
    approveBtn.onclick = async () => {
      approveBtn.disabled = true; rejectBtn.disabled = true;
      openApprovals.delete(data.pending_id);
      try {
        const r = await fetch(`/api/pending-edits/${data.pending_id}/approve`, { method: 'POST' });
        actionsRow.textContent = r.ok ? 'approved - written to disk' : 'failed to approve';
      } catch (_) { actionsRow.textContent = 'failed to approve'; }
    };
    rejectBtn.onclick = async () => {
      approveBtn.disabled = true; rejectBtn.disabled = true;
      openApprovals.delete(data.pending_id);
      try {
        await fetch(`/api/pending-edits/${data.pending_id}/reject`, { method: 'POST' });
        actionsRow.textContent = 'rejected - no changes written';
      } catch (_) { actionsRow.textContent = 'failed to reject'; }
    };
    actionsRow.appendChild(approveBtn);
    actionsRow.appendChild(rejectBtn);
    card.appendChild(actionsRow);
  }

  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderShellApprovalCard(data) {
  const w = $('welcome');
  if (w) w.remove();
  const card = document.createElement('div');
  card.className = 'diff-card shell-card';
  const where = data.cwd ? ` in ${escapeHtml(data.cwd)}` : '';
  card.innerHTML =
    `<div class="diff-card-header">shell command - waiting for your approval${where}</div>` +
    `<pre>${escapeHtml(data.command || '')}</pre>`;
  const actionsRow = document.createElement('div');
  actionsRow.className = 'diff-card-actions';
  const approveBtn = document.createElement('button');
  approveBtn.type = 'button'; approveBtn.className = 'btn send'; approveBtn.textContent = 'run it';
  const rejectBtn = document.createElement('button');
  rejectBtn.type = 'button'; rejectBtn.className = 'btn ghost'; rejectBtn.textContent = 'reject';
  trackApproval(data.pending_id, card);
  const decide = async (approved) => {
    approveBtn.disabled = true; rejectBtn.disabled = true;
    openApprovals.delete(data.pending_id);
    try {
      const r = await fetch(`/api/pending-shell/${data.pending_id}/${approved ? 'approve' : 'reject'}`, { method: 'POST' });
      actionsRow.textContent = r.ok ? (approved ? 'approved - running…' : 'rejected - not run') : 'that command is no longer waiting';
    } catch (_) {
      actionsRow.textContent = 'failed to send your answer';
    }
  };
  approveBtn.onclick = () => decide(true);
  rejectBtn.onclick = () => decide(false);
  actionsRow.append(approveBtn, rejectBtn);
  card.appendChild(actionsRow);
  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderShellResultCard(result) {
  const w = $('welcome');
  if (w) w.remove();
  const text = result || '';
  const failed = text.startsWith('error:');
  const card = document.createElement('div');
  card.className = 'diff-card shell-card';
  card.innerHTML =
    `<div class="diff-card-header">${failed ? 'shell command not run' : 'shell output'}</div>` +
    `<pre>${escapeHtml(text)}</pre>`;
  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderGeneratedFileCard(toolName, resultJson) {
  let data;
  try { data = JSON.parse(resultJson); }
  catch (_) { printLine('» ' + toolName + ': ' + resultJson, resultJson.startsWith('error') ? 'err' : 'info'); return; }
  if (!data.url) { printLine('» ' + toolName + ': ' + resultJson, 'err'); return; }

  const w = $('welcome');
  if (w) w.remove();

  const card = document.createElement('div');
  card.className = 'diff-card';

  if (data.kind === 'image') {
    card.innerHTML = `<img src="${data.url}" alt="${escapeHtml(data.prompt || 'generated image')}" style="max-width:100%;display:block;" />`;
    if (data.source || data.image_url) {
      const from = document.createElement('div');
      from.className = 'diff-card-source';
      const link = document.createElement('a');
      link.href = data.source || data.image_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = data.source ? new URL(data.source, location.href).hostname : 'image source';
      from.append(escapeHtml(data.prompt || '') ? data.prompt + ' · from ' : 'from ', link);
      card.appendChild(from);
    }
  } else if (data.kind === 'audio') {
    card.innerHTML = `<div class="diff-card-header">generated audio</div><audio controls src="${data.url}" style="width:100%"></audio>`;
  } else if (data.kind === 'document') {
    card.innerHTML =
      `<div class="diff-card-header">generated document</div>` +
      `<div class="diff-card-actions"><a class="btn send" href="${data.url}" download="${escapeHtml(data.filename || 'document')}">download ${escapeHtml(data.filename || '')}</a></div>`;
  }
  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderGeneratedVideoCard(resultJson) {
  let data;
  try { data = JSON.parse(resultJson); }
  catch (_) { printLine('» generate_video: ' + resultJson, resultJson.startsWith('error') ? 'err' : 'info'); return; }
  const files = data.files || [];
  if (!files.length) { printLine('» generate_video: no output files', 'err'); return; }

  const w = $('welcome');
  if (w) w.remove();

  for (const file of files) {
    const card = document.createElement('div');
    card.className = 'diff-card';
    if (file.kind === 'video') {
      card.innerHTML = `<video controls src="${file.url}" style="width:100%;display:block;"></video>`;
    } else {
      card.innerHTML = `<img src="${file.url}" alt="${escapeHtml(file.filename || 'generated frame')}" style="max-width:100%;display:block;" />`;
    }
    els.output.appendChild(card);
  }
  els.output.scrollTop = els.output.scrollHeight;
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
    await forkSession(idx);
    rerenderAll();
    send(newText);
  };
}

async function forkSession(uptoIdx) {
  const forkedMessages = messages.slice(0, uptoIdx);
  const parentId = currentSessionId;
  try {
    const r = await fetch('/api/chat/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: (forkedMessages[0]?.content || 'New chat').slice(0, 60),
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

const appConfig = { version: '', own_tools_models: ['Arty', 'assistant-core', 'arty/*'], inline_media_prefixes: ['/arty-files/'] };

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
    appConfig.version = data.version || '';
  } catch (_) {}
  const about = $('aboutVersion');
  if (about) about.textContent = 'starfire.ai' + (appConfig.version ? ' ' + appConfig.version : '');
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

function saveDraft() {
  try { localStorage.setItem('sf_draft_messages', JSON.stringify(messages)); } catch (_) {}
}

async function persistSession() {
  if (isTemporaryChat()) return;
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
      onChatChanged();
    }
    const folderId = isNewSession ? takePendingFolderId() : '';
    await fetch('/api/chat/sessions/' + currentSessionId, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages, endpoint_id: model ? model.endpoint_id : '', model: model ? model.id : '',
        ...currentChatControls(),
        ...(folderId ? { folder_id: folderId } : {}),
      }),
    });
    try { localStorage.removeItem('sf_draft_messages'); } catch (_) {}
    refreshSidebar();

    if (isNewSession && model && messages.length >= 2) describeNewSession(currentSessionId, model);
  } catch (_) {

  }
}

async function restoreSession() {
  if (currentSessionId) {
    try {
      const r = await fetch('/api/chat/sessions/' + currentSessionId);
      if (r.ok) {
        const session = await r.json();
        messages = session.messages || [];
        showSessionControls(session);
        selectModel(session.endpoint_id, session.model);
        rerenderAll();
        onChatChanged();
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
      opt.textContent        = m.id + (m.size ? ' (' + (m.size / 1073741824).toFixed(1) + ' GB)' : '') + ' - ' + label;
      els.modelList.appendChild(opt);
    }

    selectDefaultModel();
    renderModelPicker();
    if (!messages.length) renderWelcome();

    if (!models.length) {
      setStatus('error', 'no models configured');
      els.input.disabled   = true;
      els.sendBtn.disabled = true;
      return;
    }

    setStatus('ready', els.modelList.value);
    setOffline(false);
    warmModel(currentModel());
    els.input.disabled   = false;
    els.sendBtn.disabled = false;
    els.input.focus();
  } catch (e) {
    setStatus('error', 'cannot reach starfire backend');
    setOffline(true);
    printLine('error: ' + e.message, 'err');
  }
}

function apiMessages(list) {
  return list.map(m => {
    const message = { role: m.role, content: m.content };
    if (m.attachments && m.attachments.length) message.attachments = m.attachments.map(a => a.id);
    return message;
  });
}

async function send(text, overrideModel, extra = {}) {
  if (busy) return;
  const model = overrideModel || currentModel();
  if (!model) return;

  busy = true;
  clearFollowUps();
  els.sendBtn.disabled = true;
  els.input.disabled   = true;
  els.stopBtn.hidden    = false;
  abortController       = new AbortController();

  const userIdx = messages.length;
  const userMessage = { role: 'user', content: extra.content || text, created: new Date().toISOString() };
  if (extra.content || extra.files) Object.assign(userMessage, { display: text, files: extra.files || [] });
  if (extra.attachments && extra.attachments.length) userMessage.attachments = extra.attachments;
  messages.push(userMessage);
  const userRow = appendMessageRow('user', userIdx, null, userMessage.files, userMessage.attachments);
  userRow.content.textContent = text;
  userRow.row.classList.add('fresh');
  addActionButtons('user', userRow.row, userRow.actions, userIdx);
  saveDraft();
  const sentFromComposer = els.input.value.trim() === text;
  if (sentFromComposer) {
    els.input.value = '';
    els.input.style.height = 'auto';
  }
  const typing = showTypingIndicator(model.id);

  let aiLine = null;
  let aiActions = null;
  let aiRow = null;
  let renderer = null;
  let gotToken = false;
  const aiIdx = messages.length;

  setStatus('loading', 'waiting...');

  try {

    const r = await fetch('/api/chat_stream', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      signal:  abortController.signal,
      body:    JSON.stringify({
        endpoint_id: model.endpoint_id,
        model:       model.id,
        messages:    apiMessages(messages),
        system:      els.sysPrompt.value.trim() || undefined,
        options:     chatOptions(),
        enabled_mcp_servers: modelHasOwnTools(model) ? [] : [...enabledMcpServers],
        enabled_builtin_tools: modelHasOwnTools(model) ? [] : [...enabledBuiltinTools],
        require_edit_approval: requireEditApproval,
        require_shell_approval: requireShellApproval,
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
              typing.remove();
              const created = appendMessageRow('ai', aiIdx, model.id);
              aiLine = created.content; aiActions = created.actions; aiRow = created.row;
              aiRow.classList.add('fresh');
              aiLine.classList.add('streaming');
              renderer = createStreamRenderer(aiLine, { render: markdownRender });
            }
            renderer.push(obj.delta);
            els.output.scrollTop = els.output.scrollHeight;
          } else if (obj.tool_start) {
            printLine('» calling ' + obj.tool_start.replace(/^mcp__[^_]+__/, '') + '…', 'info');
          } else if (obj.tool_result) {
            const toolName = obj.tool_result.name;
            if (toolName === 'edit_file') {
              renderDiffCard(obj.tool_result.result);
            } else if (toolName === 'generate_image' || toolName === 'generate_audio' || toolName === 'generate_document' || toolName === 'find_image') {
              renderGeneratedFileCard(toolName, obj.tool_result.result);
            } else if (toolName === 'generate_video') {
              renderGeneratedVideoCard(obj.tool_result.result);
            } else if (toolName === 'run_shell') {
              renderShellResultCard(obj.tool_result.result);
            } else {
              printLine('» ' + toolName.replace(/^mcp__[^_]+__/, '') + ' done', 'info');
            }
          } else if (obj.shell_pending) {
            renderShellApprovalCard(obj.shell_pending);
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
      decorateCodeBlocks(aiLine, renderer.text());
      showInlineMedia(aiLine);
    }
    setStatus('ready', model.id);
    const fullText = renderer ? renderer.text() : '';
    if (fullText) {
      messages.push({ role: 'assistant', content: fullText, model: model.id, created: new Date().toISOString() });
      if (aiActions) addActionButtons('ai', aiRow, aiActions, aiIdx);
      if (callModeActive()) speakThenListen(fullText);
      else if (prefs.autoSpeak) speakText(fullText);
      if (prefs.followUps && aiRow && !callModeActive()) showFollowUps(aiRow, model);
    }
    persistSession();
    if (aiLine && fullText) verifyCodeBlocks(aiLine, aiIdx, model);
  } catch (e) {
    const aborted = e.name === 'AbortError';
    if (gotToken && aiLine) {
      renderer.finish();
      aiLine.classList.remove('streaming');
      decorateCodeBlocks(aiLine, renderer.text());
      showInlineMedia(aiLine);
      const fullText = renderer.text();
      if (fullText) {
        messages.push({ role: 'assistant', content: fullText, model: model.id, created: new Date().toISOString() });
        if (aiActions) addActionButtons('ai', aiRow, aiActions, aiIdx);
      }
    } else if (!aborted) {
      messages.splice(userIdx, 1);
      rerenderAll();
      if (sentFromComposer && !els.input.value) {
        els.input.value = text;
        els.input.style.height = Math.min(els.input.scrollHeight, COMPOSER_MAX_HEIGHT_PX) + 'px';
      }
    }
    if (aborted) {
      setStatus('ready', model.id);
      printLine('- stopped -', 'info');
      persistSession();
    } else {
      setStatus('error', 'error');
      printLine('error: ' + e.message, 'err');
    }
    if (callModeActive()) setTimeout(() => speakThenListen("Sorry, that didn't work. Try again."), 0);
  } finally {
    typing.remove();
    busy                   = false;
    abortController        = null;
    els.input.disabled     = false;
    els.sendBtn.disabled   = false;
    els.stopBtn.hidden      = true;
    els.input.focus();
  }
}

const CONTINUE_INSTRUCTION = 'Continue your previous reply exactly where it stopped. Do not repeat anything or add a preamble.';
const CHARS_PER_TOKEN_ESTIMATE = 4;
const COMPOSER_MAX_HEIGHT_PX = 240;

async function continueResponse(idx) {
  const target = messages[idx];
  const model = currentModel();
  if (busy || !target || target.role !== 'assistant' || !model) return;
  const contentEl = els.output.querySelector(`[data-msg-index="${idx}"]`);
  if (!contentEl) return;

  busy = true;
  els.sendBtn.disabled = true;
  els.stopBtn.hidden = false;
  abortController = new AbortController();
  contentEl.classList.add('streaming');
  setStatus('loading', 'continuing...');
  let streamError = null;
  const separator = /\s$/.test(target.content) ? '' : ' ';
  let added = '';
  try {
    const r = await fetch('/api/chat_stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      signal: abortController.signal,
      body: JSON.stringify({
        endpoint_id: model.endpoint_id, model: model.id,
        messages: [...apiMessages(messages.slice(0, idx + 1)), { role: 'user', content: CONTINUE_INSTRUCTION }],
        system: els.sysPrompt.value.trim() || undefined,
        options: chatOptions(),
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'server error ' + r.status);
    }
    await readProgressStream(r, obj => {
      if (obj.error) { streamError = obj.error; return; }
      if (!obj.delta) return;
      added += obj.delta;
      contentEl.innerHTML = markdownRender(target.content + separator + added);
    });
    showInlineMedia(contentEl);
    if (streamError) throw new Error(streamError);
    setStatus('ready', model.id);
  } catch (e) {
    if (e.name !== 'AbortError') {
      setStatus('error', 'error');
      printLine('error: ' + e.message, 'err');
    } else {
      setStatus('ready', model.id);
    }
  } finally {
    if (added) {
      target.content += separator + added;
      delete target.code_checks;
    }
    busy = false;
    abortController = null;
    els.sendBtn.disabled = false;
    els.stopBtn.hidden = true;
    rerenderAll();
    persistSession();
    const refreshed = els.output.querySelector(`[data-msg-index="${idx}"]`);
    if (added && refreshed) verifyCodeBlocks(refreshed, idx, model);
  }
}

function downloadText(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a   = Object.assign(document.createElement('a'), { href: url, download: filename });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

function exportChat(format = 'md') {
  if (!messages.length) return;
  const base = 'starfire-' + Date.now();
  if (format === 'json') {
    const model = currentModel();
    const chat = { title: currentChatTitle(), messages, model: model ? model.id : '' };
    downloadText(base + '.json', JSON.stringify([chat], null, 2), 'application/json');
    return;
  }
  const separator = format === 'md' ? '\n\n---\n\n' : '\n\n';
  const heading = m => format === 'md' ? '### ' + m.role : m.role.toUpperCase() + ':';
  const text = messages.map(m => heading(m) + '\n\n' + (m.display ?? m.content)).join(separator);
  downloadText(base + '.' + format, text, 'text/plain');
}

function clearChat() {
  messages = [];
  currentSessionId = null;
  showChatControls(defaultChatControls());
  try {
    localStorage.removeItem('sf_current_session');
    localStorage.removeItem('sf_draft_messages');
  } catch (_) {}
  rerenderAll();
  onChatChanged();
}

els.stopBtn.onclick = () => { if (abortController) abortController.abort(); };

els.modelList.addEventListener('change', () => {
  warmModel(currentModel());
  renderModelPicker();
  if (!messages.length) renderWelcome();
  setStatus('ready', els.modelList.value);
  checkModelToolCapability();
});

function modelHasOwnTools(model) {
  return !!model && appConfig.own_tools_models.some(pattern => globMatch(model.id, pattern));
}

async function checkModelToolCapability() {
  const model = currentModel();
  const ownTools = modelHasOwnTools(model);
  document.body.classList.toggle('own-tools', ownTools);
  const anyToolsEnabled = enabledMcpServers.size > 0 || enabledBuiltinTools.size > 0;
  if (!model || ownTools || !anyToolsEnabled) {
    els.capabilityWarning.hidden = true;
    return;
  }
  try {
    const r = await fetch(`/api/model-capabilities?provider=${encodeURIComponent(model.provider)}&model=${encodeURIComponent(model.id)}`);
    const { supports_tools, tool_tier, basic_tools } = await r.json();
    if (supports_tools === false) {
      els.capabilityWarning.textContent =
        `⚠️ ${model.id} likely doesn't support tool-calling - enabled tools may be silently ignored.`;
      els.capabilityWarning.hidden = false;
    } else if (tool_tier === 'basic') {
      const basic = new Set(basic_tools || []);
      const heldBack = [...enabledBuiltinTools].filter(name => !basic.has(name));
      const note = heldBack.length ? ` Held back for this model: ${heldBack.map(n => n.replace(/_/g, ' ')).join(', ')}.` : '';
      els.capabilityWarning.textContent =
        `ℹ️ ${model.id} is a smaller model, so it gets the basic tool set only (web search, knowledge, memory, notes, images, scheduled tasks; at most 8 tools). Models of 12B and up get everything.${note}`;
      els.capabilityWarning.hidden = false;
    } else {
      els.capabilityWarning.hidden = true;
    }
  } catch (_) {
    els.capabilityWarning.hidden = true;
  }
}

els.clearBtn.onclick  = clearChat;

els.form.addEventListener('submit', e => {
  e.preventDefault();
  const t = els.input.value.trim();
  if (!t || busy) return;
  const { overrideModel, extra } = takeComposerExtras(t);
  send(t, overrideModel, extra);
});

els.input.addEventListener('keydown', e => {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
  const wantsSend = prefs.ctrlEnterSends ? (e.ctrlKey || e.metaKey) : !(e.ctrlKey || e.metaKey);
  if (!wantsSend) return;
  e.preventDefault();
  els.form.requestSubmit();
});

els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, COMPOSER_MAX_HEIGHT_PX) + 'px';
});

try { els.sysPrompt.value = localStorage.getItem('sf_sys') || ''; } catch (_) {}
els.sysPrompt.addEventListener('input', () => onChatControlsEdited());

const SETTINGS_TAB_LOADERS = {
  chats: () => { refreshChatManagement(); },
  usage: () => { refreshUsage(); },
  presets: () => { refreshPresetList(); },
  documents: () => { refreshDocumentList(); },
  prompts: () => { refreshPromptList(); },
  tools: () => { refreshMcpServerList(); refreshMcpRepository(); },
  memory: () => { refreshMemoryList(); },
  notes: () => { refreshNoteList(); refreshNoteTemplateList(); },
  automations: () => { refreshTaskEndpointOptions(); refreshTaskList(); refreshTaskRunList(); },
  email: () => { refreshEmailAccountList(); refreshEmailRules(); },
  providers: () => { refreshEndpointList(); },
  localgen: () => { refreshLocalGen(); },
  hardware: () => { refreshHardware(); refreshOllamaStatus(); },
  accounts: () => { refreshAccounts(); },
};

function showSettingsTab(tab) {
  settingsEls.tabs.forEach(t => {
    t.classList.toggle('active', t === tab);
    if (t === tab) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current');
  });
  settingsEls.panels.forEach(p => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  const load = SETTINGS_TAB_LOADERS[tab.dataset.tab];
  if (load) load();
}

function openSettings(tabName) {
  settingsEls.modal.hidden = false;
  if (isNarrowScreen()) setSidebarOpen(false);
  const wanted = typeof tabName === 'string' ? [...settingsEls.tabs].find(t => t.dataset.tab === tabName) : null;
  const tab = wanted || [...settingsEls.tabs].find(t => t.classList.contains('active')) || settingsEls.tabs[0];
  showSettingsTab(tab);
  tab.focus();
  startNoteReminderTimer();
}
function closeSettings() {
  settingsEls.modal.hidden = true;
  stopNoteReminderTimer();
}

els.settingsBtn.onclick        = () => openSettings();
settingsEls.closeBtn.onclick   = closeSettings;
settingsEls.modal.addEventListener('click', e => {
  if (e.target === settingsEls.modal) closeSettings();
});

settingsEls.tabs.forEach(tab => {
  tab.addEventListener('click', () => showSettingsTab(tab));
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
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove(ep.label || ep.base_url)) return;
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

async function refreshMcpServerList() {
  try {
    const r = await fetch('/api/mcp/servers');
    const { servers = [] } = await r.json();
    settingsEls.mcpServerList.innerHTML = '';
    if (!servers.length) {
      settingsEls.mcpServerList.innerHTML = '<li class="settings-hint">none configured yet</li>';
      return;
    }

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
        `<span class="endpoint-meta">${s.transport && s.transport !== 'stdio' ? escapeHtml(s.url) + ' · ' : ''}${s.tool_count} tool(s)${s.connected ? '' : ' · not running'}</span>` +
        `<button class="endpoint-remove admin-only" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('input[type="checkbox"]').onchange = e => {
        if (e.target.checked) enabledMcpServers.add(s.id); else enabledMcpServers.delete(s.id);
        saveEnabledMcpServers();
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove(s.name)) return;
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

async function addMcpPreset(preset, resultEl, path, env) {
  resultEl.textContent = 'starting…';
  resultEl.className = 'settings-hint';
  try {
    const r = await fetch('/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset, path: path || undefined, env: env || undefined }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'failed to start');
    }
    const server = await r.json();
    enabledMcpServers.add(server.id);
    saveEnabledMcpServers();
    resultEl.textContent = `added - ${server.tool_count} tool(s) available`;
    resultEl.className = 'settings-hint ok';
    await refreshMcpServerList();
  } catch (e) {
    resultEl.textContent = e.message;
    resultEl.className = 'settings-hint error';
  }
}

settingsEls.addFilesystemBtn.onclick = () =>
  addMcpPreset('filesystem', settingsEls.mcpQuickAddResult, settingsEls.filesystemPath.value.trim());

let mcpRepository = [];

async function refreshMcpRepository() {
  try {
    const r = await fetch('/api/mcp/repository');
    const { repository = [] } = await r.json();
    mcpRepository = repository;
    settingsEls.mcpRepoSelect.innerHTML = '<option value="">Choose a server</option>';
    for (const entry of repository) {
      const opt = document.createElement('option');
      opt.value = entry.id;
      opt.textContent = entry.name;
      settingsEls.mcpRepoSelect.appendChild(opt);
    }
  } catch (_) {}
}

settingsEls.mcpRepoSelect.addEventListener('change', () => {
  const entry = mcpRepository.find(e => e.id === settingsEls.mcpRepoSelect.value);
  settingsEls.mcpRepoConfigureBox.hidden = !entry;
  settingsEls.mcpRepoResult.textContent = '';
  if (!entry) return;

  settingsEls.mcpRepoPathRow.hidden = !entry.needs_path;
  settingsEls.mcpRepoPath.value = '';
  settingsEls.mcpRepoPath.placeholder = 'path';

  settingsEls.mcpRepoEnvFields.innerHTML = '';
  for (const field of entry.env_fields) {
    const row = document.createElement('div');
    row.className = 'settings-row';
    const input = document.createElement('input');
    input.type = 'password';
    input.placeholder = field.label;
    input.dataset.envKey = field.key;
    input.autocomplete = 'off';
    row.appendChild(input);
    settingsEls.mcpRepoEnvFields.appendChild(row);
  }
});

settingsEls.mcpRepoAddBtn.onclick = () => {
  const entry = mcpRepository.find(e => e.id === settingsEls.mcpRepoSelect.value);
  if (!entry) return;
  const env = {};
  settingsEls.mcpRepoEnvFields.querySelectorAll('input').forEach(input => {
    env[input.dataset.envKey] = input.value;
  });
  addMcpPreset(entry.id, settingsEls.mcpRepoResult, settingsEls.mcpRepoPath.value.trim(), env).then(() => {
    settingsEls.mcpRepoSelect.value = '';
    settingsEls.mcpRepoConfigureBox.hidden = true;
  });
};

function setupDirBrowser({ browseBtn, box, pathLabel, list, selectBtn, targetInput }) {
  let current = '';

  async function load(path) {
    try {
      const url = '/api/browse-dir' + (path ? '?path=' + encodeURIComponent(path) : '');
      const r = await fetch(url);
      if (!r.ok) return;
      const data = await r.json();
      current = data.path;
      pathLabel.textContent = data.path;
      list.innerHTML = '';
      if (data.parent) {
        const up = document.createElement('li');
        up.className = 'endpoint-row';
        up.innerHTML = '<span class="endpoint-label">.. (up one level)</span>';
        up.onclick = () => load(data.parent);
        list.appendChild(up);
      }
      if (!data.entries.length && !data.parent) {
        list.innerHTML += '<li class="settings-hint">no subfolders here</li>';
      }
      for (const entry of data.entries) {
        const li = document.createElement('li');
        li.className = 'endpoint-row';
        li.innerHTML = `<span class="endpoint-label">📁 ${escapeHtml(entry.name)}</span>`;
        li.onclick = () => load(entry.path);
        list.appendChild(li);
      }
    } catch (_) {
      list.innerHTML = '<li class="settings-hint error">failed to browse</li>';
    }
  }

  browseBtn.onclick = () => {
    const wasHidden = box.hidden;
    box.hidden = !wasHidden;
    if (wasHidden) load(targetInput.value.trim());
  };
  selectBtn.onclick = () => {
    targetInput.value = current;
    box.hidden = true;
  };
}

setupDirBrowser({
  browseBtn: settingsEls.browseDirBtn, box: settingsEls.dirBrowserBox,
  pathLabel: settingsEls.dirBrowserPath, list: settingsEls.dirBrowserList,
  selectBtn: settingsEls.dirBrowserSelectBtn, targetInput: settingsEls.filesystemPath,
});

setupDirBrowser({
  browseBtn: settingsEls.comfyBrowseBtn, box: settingsEls.comfyDirBrowserBox,
  pathLabel: settingsEls.comfyDirBrowserPath, list: settingsEls.comfyDirBrowserList,
  selectBtn: settingsEls.comfyDirBrowserSelectBtn, targetInput: settingsEls.comfyCheckpointsDir,
});

function syncMcpTransportRows() {
  const remote = settingsEls.mcpTransport.value !== 'stdio';
  settingsEls.mcpCommandRow.hidden = remote;
  settingsEls.mcpArgsRow.hidden = remote;
  settingsEls.mcpUrlRow.hidden = !remote;
  settingsEls.mcpAuthRow.hidden = !remote;
}
settingsEls.mcpTransport.addEventListener('change', syncMcpTransportRows);
syncMcpTransportRows();

settingsEls.addMcpCustomBtn.onclick = async () => {
  const name = settingsEls.mcpName.value.trim();
  const transport = settingsEls.mcpTransport.value;
  const command = settingsEls.mcpCommand.value.trim();
  const args = settingsEls.mcpArgs.value.trim().split(/\s+/).filter(Boolean);
  const url = settingsEls.mcpUrl.value.trim();
  const auth = settingsEls.mcpAuthHeader.value.trim();
  if (transport === 'stdio' ? !command : !url) return;
  settingsEls.mcpCustomResult.textContent = transport === 'stdio' ? 'starting…' : 'connecting…';
  settingsEls.mcpCustomResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name || (transport === 'stdio' ? command : url), transport, command, args, url,
        headers: auth ? { Authorization: auth } : {},
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'failed to start');
    }
    const server = await r.json();
    enabledMcpServers.add(server.id);
    saveEnabledMcpServers();
    settingsEls.mcpCustomResult.textContent = `added - ${server.tool_count} tool(s) available`;
    settingsEls.mcpCustomResult.className = 'settings-hint ok';
    settingsEls.mcpName.value = '';
    settingsEls.mcpUrl.value = '';
    settingsEls.mcpAuthHeader.value = '';
    settingsEls.mcpCommand.value = '';
    settingsEls.mcpArgs.value = '';
    await refreshMcpServerList();
  } catch (e) {
    settingsEls.mcpCustomResult.textContent = e.message;
    settingsEls.mcpCustomResult.className = 'settings-hint error';
  }
};

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

settingsEls.toggleGenerateImage.checked = enabledBuiltinTools.has('generate_image');
settingsEls.toggleGenerateImage.addEventListener('change', () => {
  if (settingsEls.toggleGenerateImage.checked) enabledBuiltinTools.add('generate_image');
  else enabledBuiltinTools.delete('generate_image');
  saveEnabledBuiltinTools();
});

settingsEls.toggleFindImage.checked = enabledBuiltinTools.has('find_image');
settingsEls.toggleFindImage.addEventListener('change', () => {
  if (settingsEls.toggleFindImage.checked) enabledBuiltinTools.add('find_image');
  else enabledBuiltinTools.delete('find_image');
  saveEnabledBuiltinTools();
});

settingsEls.toggleGenerateAudio.checked = enabledBuiltinTools.has('generate_audio');
settingsEls.toggleGenerateAudio.addEventListener('change', () => {
  if (settingsEls.toggleGenerateAudio.checked) enabledBuiltinTools.add('generate_audio');
  else enabledBuiltinTools.delete('generate_audio');
  saveEnabledBuiltinTools();
});

settingsEls.toggleGenerateVideo.checked = enabledBuiltinTools.has('generate_video');
settingsEls.toggleGenerateVideo.addEventListener('change', () => {
  if (settingsEls.toggleGenerateVideo.checked) enabledBuiltinTools.add('generate_video');
  else enabledBuiltinTools.delete('generate_video');
  saveEnabledBuiltinTools();
});

settingsEls.toggleGenerateDocument.checked = enabledBuiltinTools.has('generate_document');
settingsEls.toggleGenerateDocument.addEventListener('change', () => {
  if (settingsEls.toggleGenerateDocument.checked) enabledBuiltinTools.add('generate_document');
  else enabledBuiltinTools.delete('generate_document');
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
settingsEls.shellApprovalRow.hidden = !settingsEls.toggleRunShell.checked;
settingsEls.toggleRunShell.addEventListener('change', () => {
  if (settingsEls.toggleRunShell.checked) enabledBuiltinTools.add('run_shell');
  else enabledBuiltinTools.delete('run_shell');
  settingsEls.shellApprovalRow.hidden = !settingsEls.toggleRunShell.checked;
  saveEnabledBuiltinTools();
});

let requireShellApproval = true;
try { requireShellApproval = localStorage.getItem('sf_shell_approval') !== '0'; } catch (_) {}
settingsEls.toggleShellApproval.checked = requireShellApproval;
settingsEls.toggleShellApproval.addEventListener('change', () => {
  requireShellApproval = settingsEls.toggleShellApproval.checked;
  try { localStorage.setItem('sf_shell_approval', requireShellApproval ? '1' : '0'); } catch (_) {}
});

async function openSession(id) {
  if (busy) return;
  try {
    const r = await fetch('/api/chat/sessions/' + id);
    if (!r.ok) return;
    const session = await r.json();
    currentSessionId = id;
    try { localStorage.setItem('sf_current_session', id); } catch (_) {}
    messages = session.messages || [];
    showSessionControls(session);
    selectModel(session.endpoint_id, session.model);
    rerenderAll();
    closeSettings();
    onChatChanged();
  } catch (e) {
    printLine('error: could not open chat - ' + e.message, 'err');
  }
}

let memoryCategoryFilter = '';

async function refreshMemoryList() {
  try {
    const r = await fetch('/api/memory');
    const { memories: allMemories = [] } = await r.json();
    const memories = memoryCategoryFilter ? allMemories.filter(m => m.category === memoryCategoryFilter) : allMemories;
    settingsEls.memoryList.innerHTML = '';
    if (!memories.length) {
      settingsEls.memoryList.innerHTML = `<li class="settings-hint">${allMemories.length ? 'nothing in this category' : 'nothing remembered yet'}</li>`;
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
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('input[type="checkbox"]').onchange = async e => {
        await fetch('/api/memory/' + m.id + '/pin', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pinned: e.target.checked }),
        });
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this memory')) return;
        await fetch('/api/memory/' + m.id, { method: 'DELETE' });
        await refreshMemoryList();
      };
      settingsEls.memoryList.appendChild(li);
    }
  } catch (_) {
    settingsEls.memoryList.innerHTML = '<li class="settings-hint error">failed to load memory</li>';
  }
}

document.querySelectorAll('#memoryCategoryTabs button').forEach(btn => {
  btn.addEventListener('click', () => {
    memoryCategoryFilter = btn.dataset.category;
    document.querySelectorAll('#memoryCategoryTabs button').forEach(b => b.classList.toggle('active', b === btn));
    refreshMemoryList();
  });
});

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
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove(d.filename)) return;
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
    settingsEls.documentUploadResult.textContent = `indexed - ${doc.chunk_count} chunk(s)`;
    settingsEls.documentUploadResult.className = 'settings-hint ok';
    settingsEls.documentFile.value = '';
    await refreshDocumentList();
  } catch (e) {
    settingsEls.documentUploadResult.textContent = e.message;
    settingsEls.documentUploadResult.className = 'settings-hint error';
  }
};

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

const TASK_TOOL_OPTIONS = [
  ['manage_memory', 'memory'], ['search_documents', 'documents'], ['manage_tasks', 'tasks'],
  ['manage_email', 'email'], ['manage_notes', 'notes'], ['search_web', 'web search'],
  ['find_image', 'find images'], ['run_shell', '⚠️ shell'],
];

function renderTaskToolCheckboxes() {
  const box = $('taskToolCheckboxes');
  if (!box || box.childElementCount) return;
  for (const [id, label] of TASK_TOOL_OPTIONS) {
    const wrap = document.createElement('label');
    wrap.className = 'theme-toggle';
    const input = document.createElement('input');
    input.type = 'checkbox'; input.value = id;
    wrap.appendChild(input);
    wrap.appendChild(document.createTextNode(sentenceCase(label.replace('⚠️ ', ''))));
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
        `<span class="endpoint-meta">${escapeHtml(scheduleLabel)} · ${escapeHtml(t.status)} · next ${t.next_run ? new Date(t.next_run).toLocaleString() : '-'}</span>` +
        `<button class="btn ghost" data-act="toggle">${t.status === 'active' ? 'pause' : 'resume'}</button>` +
        `<button class="btn ghost" data-act="run">run now</button>` +
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
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
        if (!await confirmRemove('this automation')) return;
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
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('[data-act="open"]').onclick = () => openEmailInbox(a.id);
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this email account')) return;
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
    await refreshEmailRules();
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

async function refreshEmailRules() {
  try {
    const accountsRes = await fetch('/api/email/accounts');
    const { accounts = [] } = await accountsRes.json();
    settingsEls.ruleAccountSelect.innerHTML = '';
    for (const a of accounts) {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.label;
      settingsEls.ruleAccountSelect.appendChild(opt);
    }

    settingsEls.ruleModelSelect.innerHTML = '';
    for (const m of models) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.dataset.endpointId = m.endpoint_id;
      opt.textContent = m.id;
      settingsEls.ruleModelSelect.appendChild(opt);
    }

    const rulesRes = await fetch('/api/email/rules');
    const { rules = [] } = await rulesRes.json();
    settingsEls.ruleList.innerHTML = '';
    if (!rules.length) {
      settingsEls.ruleList.innerHTML = '<li class="settings-hint">no rules yet</li>';
      return;
    }
    const accountById = Object.fromEntries(accounts.map(a => [a.id, a]));
    for (const rule of rules) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const accountLabel = accountById[rule.account_id]?.label || '(deleted account)';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(rule.match_field)} contains "${escapeHtml(rule.match_value)}"</span>` +
        `<span class="endpoint-meta">${escapeHtml(accountLabel)}/${escapeHtml(rule.folder)} &rarr; ${escapeHtml(rule.action)}</span>` +
        `<label class="theme-toggle"><input type="checkbox" ${rule.enabled ? 'checked' : ''} /><span>on</span></label>` +
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('input[type="checkbox"]').onchange = async e => {
        await fetch(`/api/email/rules/${rule.id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: e.target.checked }),
        });
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this rule')) return;
        await fetch('/api/email/rules/' + rule.id, { method: 'DELETE' });
        await refreshEmailRules();
      };
      settingsEls.ruleList.appendChild(li);
    }
  } catch (_) {
    settingsEls.ruleList.innerHTML = '<li class="settings-hint error">failed to load rules</li>';
  }
}

settingsEls.ruleAction.addEventListener('change', () => {
  settingsEls.ruleModelRow.hidden = settingsEls.ruleAction.value !== 'ai_summarize_note';
});

settingsEls.createRuleBtn.onclick = async () => {
  const matchValue = settingsEls.ruleMatchValue.value.trim();
  if (!matchValue) return;
  const accountId = settingsEls.ruleAccountSelect.value;
  if (!accountId) {
    settingsEls.ruleCreateResult.textContent = 'add an email account first';
    settingsEls.ruleCreateResult.className = 'settings-hint error';
    return;
  }
  const modelOpt = settingsEls.ruleModelSelect.selectedOptions[0];
  try {
    const r = await fetch('/api/email/rules', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_id: accountId, folder: settingsEls.ruleFolder.value || 'INBOX',
        match_field: settingsEls.ruleMatchField.value, match_value: matchValue,
        action: settingsEls.ruleAction.value,
        endpoint_id: modelOpt ? modelOpt.dataset.endpointId : '',
        model: modelOpt ? modelOpt.value : '',
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'create failed');
    }
    settingsEls.ruleCreateResult.textContent = 'added';
    settingsEls.ruleCreateResult.className = 'settings-hint ok';
    settingsEls.ruleMatchValue.value = '';
    await refreshEmailRules();
  } catch (e) {
    settingsEls.ruleCreateResult.textContent = e.message;
    settingsEls.ruleCreateResult.className = 'settings-hint error';
  }
};

let pendingNoteItems = [];

function renderPendingNoteItems() {
  settingsEls.noteItemsEditor.innerHTML = '';
  pendingNoteItems.forEach((item, idx) => {
    const li = document.createElement('li');
    li.className = 'note-item-row';
    li.innerHTML = `<span class="note-item-text">${escapeHtml(item.text)}</span><button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
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

async function refreshNoteTemplateList() {
  try {
    const r = await fetch('/api/note-templates');
    const { templates = [] } = await r.json();

    settingsEls.noteTemplateSelect.innerHTML = '<option value="">Start from a template</option>';
    for (const t of templates) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.name;
      settingsEls.noteTemplateSelect.appendChild(opt);
    }

    settingsEls.noteTemplateList.innerHTML = '';
    if (!templates.length) {
      settingsEls.noteTemplateList.innerHTML = '<li class="settings-hint">none saved yet</li>';
      return;
    }
    for (const t of templates) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(t.name)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(t.note_type)}</span>` +
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this template')) return;
        await fetch('/api/note-templates/' + t.id, { method: 'DELETE' });
        await refreshNoteTemplateList();
      };
      settingsEls.noteTemplateList.appendChild(li);
    }
  } catch (_) {
    settingsEls.noteTemplateList.innerHTML = '<li class="settings-hint error">failed to load templates</li>';
  }
}

settingsEls.useTemplateBtn.onclick = async () => {
  const templateId = settingsEls.noteTemplateSelect.value;
  if (!templateId) return;
  try {
    const r = await fetch('/api/note-templates');
    const { templates = [] } = await r.json();
    const t = templates.find(x => x.id === templateId);
    if (!t) return;
    settingsEls.noteTitle.value = t.title || '';
    settingsEls.noteContent.value = t.content || '';
    settingsEls.noteType.value = t.note_type || 'note';
    settingsEls.noteType.dispatchEvent(new Event('change'));
    settingsEls.noteLabel.value = t.label || '';
    settingsEls.noteColor.value = t.color || '';
    settingsEls.noteRepeat.value = t.repeat || 'none';
    pendingNoteItems = (t.items || []).map(i => ({ text: i.text, done: false }));
    renderPendingNoteItems();
  } catch (_) {}
};

settingsEls.saveAsTemplateBtn.onclick = async () => {
  const name = await showDialog({
    title: 'Save as template', text: 'Name this template.',
    input: settingsEls.noteTitle.value.trim() || 'Untitled template', okLabel: 'Save',
  });
  if (!name) return;
  try {
    const r = await fetch('/api/note-templates', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, title: settingsEls.noteTitle.value.trim(), content: settingsEls.noteContent.value.trim(),
        items: pendingNoteItems, note_type: settingsEls.noteType.value,
        label: settingsEls.noteLabel.value.trim(), color: settingsEls.noteColor.value.trim(),
        repeat: settingsEls.noteRepeat.value,
      }),
    });
    if (!r.ok) throw new Error('save failed');
    await refreshNoteTemplateList();
  } catch (_) {
    settingsEls.noteCreateResult.textContent = 'failed to save template';
    settingsEls.noteCreateResult.className = 'settings-hint error';
  }
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
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.appendChild(header);

      const aiBtn = header.querySelector('[data-act="ai-improve"]');
      if (aiBtn) {
        aiBtn.onclick = async () => {
          const model = currentModel();
          if (!model) { showToast('Select a model in the header first', 'error'); return; }
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
        if (!await confirmRemove('this note')) return;
        await fetch('/api/notes/' + n.id, { method: 'DELETE' });
        await refreshNoteList();
      };
      settingsEls.noteList.appendChild(li);
    }
  } catch (_) {
    settingsEls.noteList.innerHTML = '<li class="settings-hint error">failed to load notes</li>';
  }
}

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
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('[data-act="apply"]').onclick = () => applyPreset(p);
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this preset')) return;
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
  onChatControlsEdited();
  if (p.model) {
    const opt = [...els.modelList.options].find(o => o.value === p.model && o.dataset.endpointId === p.endpoint_id);
    if (opt) els.modelList.value = opt.value;
  }
  enabledMcpServers = new Set(p.enabled_mcp_servers || []);
  enabledBuiltinTools = new Set(p.enabled_builtin_tools || []);
  saveEnabledMcpServers();
  saveEnabledBuiltinTools();

  settingsEls.toggleManageMemory.checked = enabledBuiltinTools.has('manage_memory');
  settingsEls.toggleSearchDocuments.checked = enabledBuiltinTools.has('search_documents');
  settingsEls.toggleManageTasks.checked = enabledBuiltinTools.has('manage_tasks');
  settingsEls.toggleManageEmail.checked = enabledBuiltinTools.has('manage_email');
  settingsEls.toggleManageNotes.checked = enabledBuiltinTools.has('manage_notes');
  settingsEls.toggleSearchWeb.checked = enabledBuiltinTools.has('search_web');
  settingsEls.toggleGithubCli.checked = enabledBuiltinTools.has('github_cli');
  settingsEls.toggleDeepResearch.checked = enabledBuiltinTools.has('deep_research');
  settingsEls.toggleGenerateImage.checked = enabledBuiltinTools.has('generate_image');
  settingsEls.toggleFindImage.checked = enabledBuiltinTools.has('find_image');
  settingsEls.toggleGenerateAudio.checked = enabledBuiltinTools.has('generate_audio');
  settingsEls.toggleGenerateVideo.checked = enabledBuiltinTools.has('generate_video');
  settingsEls.toggleGenerateDocument.checked = enabledBuiltinTools.has('generate_document');
  settingsEls.toggleEditFile.checked = enabledBuiltinTools.has('edit_file');
  settingsEls.editApprovalRow.hidden = !settingsEls.toggleEditFile.checked;
  settingsEls.toggleRunShell.checked = enabledBuiltinTools.has('run_shell');
  settingsEls.shellApprovalRow.hidden = !settingsEls.toggleRunShell.checked;
  refreshMcpServerList();
}

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

async function refreshOllamaStatus() {
  try {
    const r = await fetch('/api/hardware/ollama');
    const data = await r.json();
    if (data.running) {
      settingsEls.ollamaStatus.innerHTML = '<p class="settings-hint">✓ installed and running.</p>';
      return;
    }
    if (data.installed) {
      settingsEls.ollamaStatus.innerHTML =
        '<p class="settings-hint">Installed, but not running.</p>' +
        '<button class="btn ghost" id="ollamaStartBtn" type="button">start Ollama</button>' +
        '<p class="settings-hint" id="ollamaStartMsg"></p>';
      $('ollamaStartBtn').onclick = async (e) => {
        e.target.disabled = true;
        e.target.textContent = 'starting…';
        try {
          const res = await fetch('/api/hardware/ollama/start', { method: 'POST' });
          const body = await res.json().catch(() => ({}));
          if (res.ok) {
            $('ollamaStartMsg').textContent = body.message || 'started.';
            await refreshOllamaStatus();
            await refreshHardware();
            return;
          }
          $('ollamaStartMsg').textContent = body.detail || 'failed to start.';
        } catch (_) {
          $('ollamaStartMsg').textContent = 'failed to start.';
        }
        e.target.disabled = false;
        e.target.textContent = 'start Ollama';
      };
      return;
    }

    const install = data.install || {};
    let html = '<p class="settings-hint">Not installed.</p>';
    if (install.method === 'command' && install.command) {
      html += `<p class="settings-hint"><code>${escapeHtml(install.command)}</code></p>`;
    } else if (install.url) {
      html += `<p class="settings-hint"><a href="${escapeHtml(install.url)}" target="_blank" rel="noopener">download Ollama</a></p>`;
    }
    if (install.note) {
      html += `<p class="settings-hint">${escapeHtml(install.note)}</p>`;
    }
    settingsEls.ollamaStatus.innerHTML = html;
  } catch (_) {
    settingsEls.ollamaStatus.innerHTML = '<p class="settings-hint error">failed to check Ollama status</p>';
  }
}

async function refreshHardware() {
  try {
    const r = await fetch('/api/hardware');
    const data = await r.json();
    const memLine = data.vram_gb
      ? `${data.vram_gb} GB VRAM detected (GPU)`
      : data.ram_gb ? `${data.ram_gb} GB system RAM (no GPU detected - CPU/unified-memory sizing)` : 'could not detect memory';
    const cpuLine = data.cpu_model
      ? `CPU: ${escapeHtml(data.cpu_model)}${data.cpu_cores ? ` (${data.cpu_cores} threads` : ''}${data.cpu_max_mhz ? `, up to ${(data.cpu_max_mhz / 1000).toFixed(2)} GHz)` : (data.cpu_cores ? ')' : '')}`
      : '';
    const gpuLine = data.gpu_name ? `GPU: ${escapeHtml(data.gpu_name)}` : '';
    const ramLine = data.ram_speed_mts ? `RAM: ${escapeHtml(data.ram_type || '')} @ ${data.ram_speed_mts} MT/s` : '';
    settingsEls.hardwareInfo.innerHTML =
      `<p class="settings-hint">${memLine}, ~${data.budget_gb} GB usable budget for a model.</p>` +
      [cpuLine, gpuLine, ramLine].filter(Boolean).map(l => `<p class="settings-hint">${l}</p>`).join('') +
      (data.speed_note ? `<p class="settings-hint">${escapeHtml(data.speed_note)}</p>` : '');
    settingsEls.hardwareModelList.innerHTML = '';
    for (const c of data.all_candidates || []) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.style.flexDirection = 'column';
      li.style.alignItems = 'stretch';
      li.innerHTML =
        `<div class="endpoint-row" style="border:none;padding:0;">` +
          `<span class="endpoint-label">${c.fits ? '✓' : '✗'} ${c.name}</span>` +
          `<span class="endpoint-meta">~${c.size_gb} GB${c.fits ? '' : ' - likely too large'}</span>` +
          `<button class="btn ghost" data-act="pull">pull</button>` +
        `</div>` +
        `<progress class="pull-progress" max="100" hidden></progress>`;
      const statusEl = li.querySelector('.endpoint-meta');
      const progressEl = li.querySelector('.pull-progress');
      li.querySelector('[data-act="pull"]').onclick = e => pullModel(c.name, e.target, statusEl, progressEl);
      settingsEls.hardwareModelList.appendChild(li);
    }
  } catch (_) {
    settingsEls.hardwareInfo.innerHTML = '<p class="settings-hint error">failed to detect hardware</p>';
  }
}

async function refreshComfyUI() {
  try {
    const cfgRes = await fetch('/api/comfyui/config');
    const cfg = await cfgRes.json();
    settingsEls.comfyBaseUrl.value = cfg.base_url || '';
    settingsEls.comfyCheckpointsDir.value = cfg.checkpoints_dir || '';
    settingsEls.comfyNegativePrompt.value = cfg.default_negative_prompt || '';

    const statusRes = await fetch('/api/comfyui/status');
    const status = await statusRes.json();
    settingsEls.comfyStatus.textContent = status.connected
      ? `connected - ${status.checkpoints.length} checkpoint(s) available`
      : 'not connected';
    settingsEls.comfyStatus.className = 'settings-hint ' + (status.connected ? 'ok' : '');

    settingsEls.comfyCheckpointSelect.innerHTML = '<option value="">Default checkpoint</option>';
    for (const name of status.checkpoints) {
      const opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      if (name === cfg.default_checkpoint) opt.selected = true;
      settingsEls.comfyCheckpointSelect.appendChild(opt);
    }
  } catch (_) {
    settingsEls.comfyStatus.textContent = 'failed to load ComfyUI status';
    settingsEls.comfyStatus.className = 'settings-hint error';
  }
}

settingsEls.comfyDetectBtn.onclick = refreshComfyUI;

settingsEls.comfySaveBtn.onclick = async () => {
  try {
    await fetch('/api/comfyui/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: settingsEls.comfyBaseUrl.value.trim(),
        checkpoints_dir: settingsEls.comfyCheckpointsDir.value.trim(),
        default_checkpoint: settingsEls.comfyCheckpointSelect.value,
        default_negative_prompt: settingsEls.comfyNegativePrompt.value.trim(),
      }),
    });
    settingsEls.comfySaveResult.textContent = 'saved';
    settingsEls.comfySaveResult.className = 'settings-hint ok';
    await refreshComfyUI();
  } catch (e) {
    settingsEls.comfySaveResult.textContent = 'failed to save';
    settingsEls.comfySaveResult.className = 'settings-hint error';
  }
};

settingsEls.comfyPullBtn.onclick = async () => {
  const url = settingsEls.comfyPullUrl.value.trim();
  const filename = settingsEls.comfyPullFilename.value.trim();
  if (!url || !filename) return;
  settingsEls.comfyPullBtn.disabled = true;
  settingsEls.comfyPullStatus.textContent = 'starting…';
  try {
    const r = await fetch('/api/comfyui/pull-checkpoint', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, filename }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'failed to start'); }
    await readProgressStream(r, (obj) => {
      if (obj.error) { settingsEls.comfyPullStatus.textContent = 'error: ' + obj.error; hideProgressBar(settingsEls.comfyPullProgress); return; }
      if (obj.done) { settingsEls.comfyPullStatus.textContent = 'done'; hideProgressBar(settingsEls.comfyPullProgress); return; }
      updateProgressBar(settingsEls.comfyPullProgress, obj.downloaded, obj.total);
      const pct = obj.total ? Math.round(100 * obj.downloaded / obj.total) + '%' : (obj.downloaded / 1e6).toFixed(1) + ' MB';
      settingsEls.comfyPullStatus.textContent = 'downloading… ' + pct;
    });
    await refreshComfyUI();
  } catch (e) {
    settingsEls.comfyPullStatus.textContent = 'error: ' + e.message;
    hideProgressBar(settingsEls.comfyPullProgress);
  } finally {
    settingsEls.comfyPullBtn.disabled = false;
  }
};

function updateProgressBar(el, downloaded, total) {
  if (!el) return;
  el.hidden = false;
  if (total) el.value = Math.min(100, Math.round(100 * downloaded / total));
  else el.removeAttribute('value');
}
function hideProgressBar(el) { if (el) el.hidden = true; }

async function readProgressStream(response, onEvent) {
  const reader = response.body.getReader();
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
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch (_) {}
    }
  }
}

async function refreshPiper() {
  try {
    const r = await fetch('/api/piper/config');
    const cfg = await r.json();
    settingsEls.piperVoicePath.value = cfg.voice_model_path || '';
    if (!cfg.installed) {
      settingsEls.piperSaveResult.textContent = 'piper not installed yet - restart starfire.ai to trigger auto-install';
      settingsEls.piperSaveResult.className = 'settings-hint error';
    }
  } catch (_) {}
}

settingsEls.piperSaveBtn.onclick = async () => {
  try {
    await fetch('/api/piper/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_model_path: settingsEls.piperVoicePath.value.trim() }),
    });
    settingsEls.piperSaveResult.textContent = 'saved';
    settingsEls.piperSaveResult.className = 'settings-hint ok';
  } catch (_) {
    settingsEls.piperSaveResult.textContent = 'failed to save';
    settingsEls.piperSaveResult.className = 'settings-hint error';
  }
};

settingsEls.piperPullBtn.onclick = async () => {
  const voiceName = settingsEls.piperVoiceName.value.trim();
  const quality = settingsEls.piperVoiceQuality.value;
  const voicesDir = settingsEls.piperVoicesDirForPull.value.trim();
  if (!voiceName || !voicesDir) return;
  settingsEls.piperPullBtn.disabled = true;
  settingsEls.piperPullStatus.textContent = 'starting…';
  try {
    await fetch('/api/piper/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voices_dir: voicesDir }),
    });
    const r = await fetch('/api/piper/pull-voice', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice_name: voiceName, quality }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'failed to start'); }
    let finalPath = '';
    await readProgressStream(r, (obj) => {
      if (obj.error) { settingsEls.piperPullStatus.textContent = 'error: ' + obj.error; hideProgressBar(settingsEls.piperPullProgress); return; }
      if (obj.done) { settingsEls.piperPullStatus.textContent = 'done'; hideProgressBar(settingsEls.piperPullProgress); finalPath = obj.path; return; }
      updateProgressBar(settingsEls.piperPullProgress, obj.downloaded, obj.total);
      const pct = obj.total ? Math.round(100 * obj.downloaded / obj.total) + '%' : (obj.downloaded / 1e6).toFixed(1) + ' MB';
      settingsEls.piperPullStatus.textContent = 'downloading… ' + pct;
    });
    if (finalPath) {
      settingsEls.piperVoicePath.value = finalPath;
      await fetch('/api/piper/config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_model_path: finalPath }),
      });
    }
  } catch (e) {
    settingsEls.piperPullStatus.textContent = 'error: ' + e.message;
    hideProgressBar(settingsEls.piperPullProgress);
  } finally {
    settingsEls.piperPullBtn.disabled = false;
  }
};

async function refreshWorkflowList() {
  try {
    const r = await fetch('/api/custom-workflows');
    const { workflows = [] } = await r.json();
    settingsEls.workflowList.innerHTML = '';
    if (!workflows.length) {
      settingsEls.workflowList.innerHTML = '<li class="settings-hint">none saved yet</li>';
      return;
    }
    for (const w of workflows) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(w.name)}</span>` +
        `<span class="endpoint-meta">node ${escapeHtml(w.prompt_node_id)} / ${escapeHtml(w.prompt_input_key)}</span>` +
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this workflow')) return;
        await fetch('/api/custom-workflows/' + w.id, { method: 'DELETE' });
        await refreshWorkflowList();
      };
      settingsEls.workflowList.appendChild(li);
    }
  } catch (_) {
    settingsEls.workflowList.innerHTML = '<li class="settings-hint error">failed to load workflows</li>';
  }
}

settingsEls.saveWorkflowBtn.onclick = async () => {
  const name = settingsEls.workflowName.value.trim();
  const promptNodeId = settingsEls.workflowPromptNode.value.trim();
  const promptInputKey = settingsEls.workflowPromptKey.value.trim() || 'text';
  if (!name || !promptNodeId) {
    settingsEls.workflowSaveResult.textContent = 'name and prompt node id are required';
    settingsEls.workflowSaveResult.className = 'settings-hint error';
    return;
  }
  let workflow;
  try {
    workflow = JSON.parse(settingsEls.workflowJson.value);
  } catch (e) {
    settingsEls.workflowSaveResult.textContent = 'invalid JSON: ' + e.message;
    settingsEls.workflowSaveResult.className = 'settings-hint error';
    return;
  }
  try {
    const r = await fetch('/api/custom-workflows', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, workflow, prompt_node_id: promptNodeId, prompt_input_key: promptInputKey }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'save failed'); }
    settingsEls.workflowSaveResult.textContent = 'saved';
    settingsEls.workflowSaveResult.className = 'settings-hint ok';
    settingsEls.workflowName.value = '';
    settingsEls.workflowJson.value = '';
    settingsEls.workflowPromptNode.value = '';
    await refreshWorkflowList();
  } catch (e) {
    settingsEls.workflowSaveResult.textContent = e.message;
    settingsEls.workflowSaveResult.className = 'settings-hint error';
  }
};

function refreshLocalGen() {
  refreshComfyUI();
  refreshPiper();
  refreshWorkflowList();
}

settingsEls.downloadBackupBtn.onclick = () => {
  window.location.href = '/api/backup';
};

settingsEls.restoreBackupBtn.onclick = async () => {
  const file = settingsEls.restoreFile.files[0];
  if (!file) return;
  const confirmed = await showDialog({
    title: 'Restore this backup?',
    text: 'This overwrites your current data with the contents of this backup. This cannot be undone.',
    okLabel: 'Restore', danger: true,
  });
  if (!confirmed) return;
  settingsEls.restoreResult.textContent = 'restoring…';
  settingsEls.restoreResult.className = 'settings-hint';
  try {
    const formData = new FormData();
    formData.append('file', file);
    const r = await fetch('/api/restore', { method: 'POST', body: formData });
    if (!r.ok) throw new Error('restore failed');
    settingsEls.restoreResult.textContent = 'restored - reloading…';
    settingsEls.restoreResult.className = 'settings-hint ok';
    setTimeout(() => window.location.reload(), 1200);
  } catch (e) {
    settingsEls.restoreResult.textContent = e.message;
    settingsEls.restoreResult.className = 'settings-hint error';
  }
};

const accountEls = {
  form: $('accountForm'), name: $('newAccountName'), email: $('newAccountEmail'),
  password: $('newAccountPassword'), role: $('newAccountRole'), result: $('accountFormResult'),
  list: $('accountList'), submit: $('createAccountBtn'),
};

function setAccountResult(text, kind) {
  accountEls.result.textContent = text;
  accountEls.result.className = 'settings-hint' + (kind ? ' ' + kind : '');
}

async function accountRequest(method, url, body) {
  const r = await fetch(url, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `failed (${r.status})`);
  return data;
}

async function refreshAccounts() {
  if (!currentUser || currentUser.role !== 'admin' || !accountEls.list) return;
  try {
    const { users } = await accountRequest('GET', '/api/auth/users');
    accountEls.list.innerHTML = '';
    for (const user of users) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const isSelf = user.id === currentUser.id;
      const when = user.created ? new Date(user.created).toLocaleDateString() : '';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(user.name)} <span class="endpoint-meta">${escapeHtml(user.email)}${isSelf ? ' · you' : ''}${user.disabled ? ' · disabled' : ''}${when ? ' · since ' + escapeHtml(when) : ''}</span></span>` +
        `<select class="account-role"><option value="user">user</option><option value="admin">admin</option></select>` +
        `<button class="btn ghost account-reset" type="button">reset password</button>` +
        `<button class="btn ghost account-toggle" type="button">${user.disabled ? 'enable' : 'disable'}</button>` +
        `<button class="endpoint-remove" type="button" title="Delete this account and its data" aria-label="Delete this account and its data">×</button>`;
      const role = li.querySelector('.account-role');
      role.value = user.role;
      role.disabled = isSelf;
      role.onchange = async () => {
        try { await accountRequest('PATCH', `/api/auth/users/${user.id}`, { role: role.value }); setAccountResult(`${user.email} is now ${role.value}`, 'ok'); }
        catch (e) { setAccountResult(e.message, 'error'); }
        refreshAccounts();
      };
      li.querySelector('.account-reset').onclick = async () => {
        const password = await showDialog({ title: `new password for ${user.email}`, text: 'At least 10 characters. Every browser signed in to this account will be logged out.', input: '', inputType: 'password', placeholder: 'new password', okLabel: 'set password' });
        if (!password) return;
        try { await accountRequest('POST', `/api/auth/users/${user.id}/password`, { password }); setAccountResult(`password set for ${user.email}`, 'ok'); }
        catch (e) { setAccountResult(e.message, 'error'); }
      };
      const toggle = li.querySelector('.account-toggle');
      toggle.disabled = isSelf;
      toggle.onclick = async () => {
        try { await accountRequest('PATCH', `/api/auth/users/${user.id}`, { disabled: !user.disabled }); setAccountResult(`${user.email} ${user.disabled ? 'enabled' : 'disabled'}`, 'ok'); }
        catch (e) { setAccountResult(e.message, 'error'); }
        refreshAccounts();
      };
      const remove = li.querySelector('.endpoint-remove');
      remove.disabled = isSelf;
      remove.onclick = async () => {
        const ok = await showDialog({ title: `delete ${user.email}?`, text: 'Their chats, memory, knowledge, notes, automations, email accounts and prompts are deleted with the account. This cannot be undone.', okLabel: 'delete account', danger: true });
        if (!ok) return;
        try { await accountRequest('DELETE', `/api/auth/users/${user.id}`); setAccountResult(`${user.email} deleted`, 'ok'); }
        catch (e) { setAccountResult(e.message, 'error'); }
        refreshAccounts();
      };
      accountEls.list.appendChild(li);
    }
  } catch (e) {
    accountEls.list.innerHTML = `<li class="settings-hint error">${escapeHtml(e.message)}</li>`;
  }
}

if (accountEls.form) {
  accountEls.form.addEventListener('submit', async event => {
    event.preventDefault();
    const body = {
      name: accountEls.name.value.trim(), email: accountEls.email.value.trim(),
      password: accountEls.password.value, role: accountEls.role.value,
    };
    if (!body.email || !body.password) { setAccountResult('email and password are required', 'error'); return; }
    accountEls.submit.disabled = true;
    setAccountResult('adding…');
    try {
      const user = await accountRequest('POST', '/api/auth/users', body);
      accountEls.form.reset();
      setAccountResult(`added ${user.email} (${user.role}) - send them the password; they can change it under Settings → Account`, 'ok');
      refreshAccounts();
    } catch (e) {
      setAccountResult(e.message, 'error');
    } finally {
      accountEls.submit.disabled = false;
    }
  });
}

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
      `<button class="btn ghost compare-remove" type="button" title="Remove column" aria-label="Remove column">×</button>` +
    `</div>` +
    `<div class="compare-column-output line ai"></div>`;
  col.querySelector('.compare-remove').onclick = () => {
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
  let full = '';
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
          if (obj.delta) { full += obj.delta; output.textContent = full; }
          else if (obj.error) { full += '\n[error: ' + obj.error + ']'; output.textContent = full; }
        } catch (_) {}
      }
    }
    renderAiContent(output, full);
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

const paletteEls = { overlay: $('paletteOverlay'), input: $('paletteInput'), list: $('paletteList') };
let paletteActions = [];
let paletteActiveIndex = 0;

function buildPaletteActions() {
  paletteActions = [
    { label: 'New chat', run: clearChat },
    { label: 'Export chat as markdown', run: () => exportChat('md') },
    { label: 'Export chat as JSON', run: () => exportChat('json') },
    { label: 'Toggle sidebar', run: toggleSidebar },
    { label: 'Toggle chat controls', run: toggleControls },
    { label: 'Toggle temporary chat', run: () => $('tempChatToggle').click() },
    { label: 'Archived chats', run: openArchived },
    { label: 'New folder', run: createFolder },
    { label: 'Open settings', run: openSettings },
    { label: 'Compare models', run: openCompare },
    { label: 'Toggle light/dark theme', run: toggleLightDark },
    { label: 'Keyboard shortcuts', run: openShortcuts },
  ];
  const isAdmin = document.body.classList.contains('is-admin');
  for (const tab of document.querySelectorAll('.settings-tab')) {
    if (tab.classList.contains('admin-only') && !isAdmin) continue;
    paletteActions.push({
      label: 'Settings → ' + tab.textContent,
      run: () => openSettings(tab.dataset.tab),
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
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', String(idx === 0));
    if (idx === 0) li.classList.add('active');
    li.onclick = () => runPaletteAction(action);
    paletteEls.list.appendChild(li);
  });
  if (!matches.length) paletteEls.list.innerHTML = '<li class="palette-empty">No matching commands</li>';
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
  items.forEach((li, idx) => {
    li.classList.toggle('active', idx === paletteActiveIndex);
    li.setAttribute('aria-selected', String(idx === paletteActiveIndex));
  });
  if (items[paletteActiveIndex]) items[paletteActiveIndex].scrollIntoView({ block: 'nearest' });
});

function closeTopmostLayer() {
  if (!paletteEls.overlay.hidden) { closePalette(); return true; }
  if (!$('dialogModal').hidden) return false;
  if (document.querySelector('.menu:not([hidden]), .model-menu:not([hidden]), .context-menu:not([hidden])')) {
    closeAllMenus();
    return true;
  }
  if (!$('shortcutsModal').hidden) { closeShortcuts(); return true; }
  if (!compareEls.modal.hidden) { closeCompare(); return true; }
  if (!archivedEls.modal.hidden) { archivedEls.modal.hidden = true; return true; }
  if (!settingsEls.modal.hidden) { closeSettings(); return true; }
  if (!$('controlsPanel').hidden) { toggleControls(false); return true; }
  return false;
}

document.addEventListener('keydown', e => {
  const key = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && key === 'k') {
    e.preventDefault();
    if (paletteEls.overlay.hidden) openPalette(); else closePalette();
  } else if ((e.metaKey || e.ctrlKey) && key === '/') {
    e.preventDefault();
    if ($('shortcutsModal').hidden) openShortcuts(); else closeShortcuts();
  } else if (key === 'escape' && !e.shiftKey && !e.defaultPrevented) {
    if (closeTopmostLayer()) {
      e.preventDefault();
    } else if (busy && abortController && $('dialogModal').hidden) {
      e.preventDefault();
      abortController.abort();
    }
  }
});

const LAYER_SELECTOR = '.palette-overlay:not([hidden]), .modal-overlay:not([hidden])';
const FOCUSABLE_SELECTOR = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
const LAYER_ORDER = ['settingsModal', 'archivedModal', 'compareModal', 'shortcutsModal', 'dialogModal', 'paletteOverlay'];
let focusBeforeLayers = null;
const focusInsideLayer = {};

function topLayer() {
  const open = [...document.querySelectorAll(LAYER_SELECTOR)];
  open.sort((x, y) => LAYER_ORDER.indexOf(x.id) - LAYER_ORDER.indexOf(y.id));
  return open[open.length - 1] || null;
}

function focusableIn(root) {
  return [...root.querySelectorAll(FOCUSABLE_SELECTOR)].filter(el => !el.disabled && el.offsetParent !== null);
}

document.addEventListener('focusin', e => {
  const layer = e.target.closest ? e.target.closest('.palette-overlay, .modal-overlay') : null;
  if (layer) focusInsideLayer[layer.id] = e.target;
  else focusBeforeLayers = e.target;
});

document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const layer = topLayer();
  if (!layer) return;
  const items = focusableIn(layer);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (!layer.contains(document.activeElement)) {
    e.preventDefault();
    first.focus();
  } else if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

function returnFocusAfterClose(closed) {
  delete focusInsideLayer[closed.id];
  if (closed.contains(document.activeElement) === false && document.activeElement !== document.body) return;
  const layer = topLayer();
  const remembered = layer ? (focusInsideLayer[layer.id] || focusableIn(layer)[0]) : focusBeforeLayers;
  const isVisible = el => !!el && el.isConnected && el.offsetParent !== null;
  const menuOpener = remembered && remembered.closest ? remembered.closest('.menu-wrap') : null;
  const target = [remembered, menuOpener && menuOpener.querySelector('button'), els.input].find(isVisible);
  if (target) target.focus();
}

const layerObserver = new MutationObserver(records => {
  for (const record of records) {
    if (record.target.hidden) returnFocusAfterClose(record.target);
  }
});
document.querySelectorAll('.palette-overlay, .modal-overlay').forEach(layer => {
  layerObserver.observe(layer, { attributes: true, attributeFilter: ['hidden'] });
});

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

async function pullModel(name, btn, statusEl, progressEl) {
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
    let sawError = null;
    await readProgressStream(r, (obj) => {
      if (obj.error) { sawError = obj.error; return; }
      if (obj.status) {
        let text = obj.status;
        if (obj.total && obj.completed) {
          text += ' ' + Math.round(100 * obj.completed / obj.total) + '%';
          updateProgressBar(progressEl, obj.completed, obj.total);
        } else {
          updateProgressBar(progressEl, 0, 0);
        }
        statusEl.textContent = text;
      }
    });
    if (sawError) throw new Error(sawError);
    statusEl.textContent = 'done';
    btn.textContent = 'pulled';
    hideProgressBar(progressEl);
  } catch (e) {
    statusEl.textContent = 'error: ' + e.message;
    btn.disabled = false;
    hideProgressBar(progressEl);
  }
}

const MAX_TAGS = 3;
const MAX_TAG_CHARS = 24;
const MAX_FOLLOW_UPS = 3;
const MAX_FOLLOW_UP_CHARS = 140;

async function askHelper(model, prompt) {
  const r = await fetch('/api/quick-complete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id, prompt, helper: true }),
  });
  if (!r.ok) throw new Error(`helper request failed (${r.status})`);
  const { text } = await r.json();
  return (text || '').trim();
}

function conversationExcerpt(count) {
  return messages.slice(-count).map(m => `${m.role}: ${m.content}`).join('\n').slice(0, 2000);
}

async function describeNewSession(sessionId, model) {
  if (prefs.autoTitle) await generateSessionTitle(sessionId, model);
  if (prefs.autoTags) await generateSessionTags(sessionId, model);
}

async function updateSession(sessionId, fields) {
  await fetch('/api/chat/sessions/' + sessionId, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  refreshSidebar();
}

async function generateSessionTitle(sessionId, model) {
  try {
    const text = await askHelper(model,
      `Give this conversation a short title (3-6 words, no quotes, no trailing punctuation):\n\n${conversationExcerpt(2)}`);
    const title = text.replace(/^["']|["']$/g, '').replace(/[.!?]+$/, '').slice(0, 60);
    if (title) await updateSession(sessionId, { title });
  } catch (e) {
    console.warn('starfire.ai: no title generated', e);
  }
}

function parseTags(text) {
  const tags = text.toLowerCase().split(/[,\n]/)
    .map(t => t.replace(/^[\s#*\-\d.)]+/, '').replace(/[^a-z0-9 -]/g, '').trim().replace(/\s+/g, '-'))
    .filter(t => t && t.length <= MAX_TAG_CHARS);
  return [...new Set(tags)].slice(0, MAX_TAGS);
}

async function generateSessionTags(sessionId, model) {
  try {
    const text = await askHelper(model,
      `Give 1 to 3 short, lowercase topic tags for this conversation (for example: networking, cooking, python). ` +
      `Reply with only the tags, separated by commas.\n\n${conversationExcerpt(2)}`);
    const tags = parseTags(text);
    if (tags.length) await updateSession(sessionId, { tags });
  } catch (e) {
    console.warn('starfire.ai: no tags generated', e);
  }
}

function parseFollowUps(text) {
  return text.split('\n')
    .map(line => line.replace(/^[\s\-*•\d.)]+/, '').replace(/^["']|["']$/g, '').trim())
    .filter(line => line.length > 3 && line.length <= MAX_FOLLOW_UP_CHARS)
    .slice(0, MAX_FOLLOW_UPS);
}

function clearFollowUps() {
  document.querySelectorAll('.follow-ups').forEach(el => el.remove());
}

async function showFollowUps(row, model) {
  const messageCount = messages.length;
  try {
    const text = await askHelper(model,
      `Suggest ${MAX_FOLLOW_UPS} short follow-up questions the user might ask next, written as the user, ` +
      `one per line, with no numbering or extra text.\n\n${conversationExcerpt(4)}`);
    const questions = parseFollowUps(text);
    if (!questions.length || busy || messages.length !== messageCount || !row.isConnected) return;
    clearFollowUps();
    const box = document.createElement('div');
    box.className = 'follow-ups';
    for (const question of questions) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'follow-up';
      btn.textContent = question;
      btn.onclick = () => { clearFollowUps(); send(question); };
      box.appendChild(btn);
    }
    row.appendChild(box);
  } catch (e) {
    console.warn('starfire.ai: no follow-up suggestions', e);
  }
}

const THEME_FADE_MS = 260;
const systemPrefersLight = window.matchMedia('(prefers-color-scheme: light)');
const themeInputs = document.querySelectorAll('input[name="themeChoice"]');

function storedTheme() {
  try {
    return themeChoiceFrom(key => localStorage.getItem(key));
  } catch (_) {
    return 'dark';
  }
}

function isLightTheme(choice) {
  return themeIsLight(choice, systemPrefersLight.matches);
}

function applyTheme(choice, animate = false) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add('theme-fade');
    setTimeout(() => root.classList.remove('theme-fade'), THEME_FADE_MS);
  }
  root.classList.toggle('light', isLightTheme(choice));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', getComputedStyle(root).getPropertyValue('--bg').trim());
  themeInputs.forEach(input => { input.checked = input.value === choice; });
}

function setTheme(choice) {
  try {
    localStorage.setItem('sf_theme', choice);
    localStorage.setItem('sf_light', choice === 'light' ? '1' : '0');
  } catch (_) {}
  applyTheme(choice, true);
}

function toggleLightDark() {
  setTheme(isLightTheme(storedTheme()) ? 'dark' : 'light');
}

applyTheme(storedTheme());
themeInputs.forEach(input => {
  input.addEventListener('change', () => { if (input.checked) setTheme(input.value); });
});
systemPrefersLight.addEventListener('change', () => {
  if (storedTheme() === 'system') applyTheme('system', true);
});

loadAppConfig().then(() => loadModels()).then(() => restoreSession()).then(() => {
  checkModelToolCapability();
  restorePendingApprovals();
});

setUpLogout().catch(error => console.error('starfire.ai: could not check the login', error));
