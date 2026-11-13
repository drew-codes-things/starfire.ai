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
  memoryAddResult: $('memoryAddResult'),
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
let sideChat = null;

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
