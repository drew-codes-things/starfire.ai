'use strict';

const PREF_DEFAULTS = {
  wideChat: false,
  autoTitle: true,
  autoTags: true,
  followUps: true,
  ctrlEnterSends: false,
  autoSpeak: false,
  showSuggestions: true,
  userBubbles: true,
  checkCode: true,
};

const prefs = { ...PREF_DEFAULTS };
try { Object.assign(prefs, JSON.parse(localStorage.getItem('sf_prefs') || '{}')); } catch (_) {}

function savePrefs() {
  try { localStorage.setItem('sf_prefs', JSON.stringify(prefs)); } catch (_) {}
}

function applyPrefs() {
  document.documentElement.classList.toggle('wide-chat', !!prefs.wideChat);
}

document.querySelectorAll('[data-pref]').forEach(input => {
  input.checked = !!prefs[input.dataset.pref];
  input.addEventListener('change', () => {
    prefs[input.dataset.pref] = input.checked;
    savePrefs();
    applyPrefs();
    rerenderAll();
  });
});
applyPrefs();

const NARROW_SCREEN_PX = 860;
const SEARCH_DEBOUNCE_MS = 250;
const MAX_ATTACHMENT_CHARS = 60000;
const SCROLL_BUTTON_THRESHOLD_PX = 300;
const DAY_MS = 86400000;

function isNarrowScreen() { return window.innerWidth <= NARROW_SCREEN_PX; }

async function apiJson(url, options = {}) {
  const init = { ...options };
  if (init.body !== undefined && typeof init.body !== 'string' && !(init.body instanceof FormData)) {
    init.body = JSON.stringify(init.body);
    init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
  }
  const r = await fetch(url, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'server error ' + r.status);
  return data;
}

function closeAllMenus(except) {
  document.querySelectorAll('.menu, .model-menu, .context-menu').forEach(m => {
    if (m !== except) m.hidden = true;
  });
  $('modelPickerBtn').setAttribute('aria-expanded', String(!$('modelMenu').hidden));
}

document.addEventListener('click', e => {
  if (!e.target.closest('.menu-wrap, .model-picker, .context-menu, .chat-item-menu-btn, .folder-menu-btn')) closeAllMenus();
});

const dialogEls = {
  modal: $('dialogModal'), title: $('dialogTitle'), text: $('dialogText'),
  input: $('dialogInput'), ok: $('dialogOkBtn'), cancel: $('dialogCancelBtn'),
};

function showDialog({ title, text = '', input, okLabel = 'ok', danger = false, allowEmpty = false }) {
  return new Promise(resolve => {
    dialogEls.title.textContent = title;
    dialogEls.text.textContent = text;
    dialogEls.input.hidden = input === undefined;
    dialogEls.input.value = input || '';
    dialogEls.ok.textContent = okLabel;
    dialogEls.ok.className = danger ? 'btn' : 'btn send';
    dialogEls.modal.hidden = false;
    (input === undefined ? dialogEls.ok : dialogEls.input).focus();

    const finish = value => {
      dialogEls.modal.hidden = true;
      dialogEls.ok.onclick = dialogEls.cancel.onclick = dialogEls.input.onkeydown = dialogEls.modal.onkeydown = null;
      resolve(value);
    };
    dialogEls.ok.onclick = () => {
      if (input === undefined) { finish(true); return; }
      const typed = dialogEls.input.value.trim();
      finish(typed || (allowEmpty ? '' : null));
    };
    dialogEls.cancel.onclick = () => finish(null);
    dialogEls.input.onkeydown = e => { if (e.key === 'Enter') dialogEls.ok.click(); };
    dialogEls.modal.onkeydown = e => { if (e.key === 'Escape') finish(null); };
  });
}

const tempChatToggle = $('tempChatToggle');

function isTemporaryChat() { return tempChatToggle.checked; }

tempChatToggle.addEventListener('change', () => {
  $('tempBanner').hidden = !tempChatToggle.checked;
  clearChat();
});

const sidebarState = {
  sessions: [],
  folders: [],
  query: '',
  expandedFolders: new Set(),
};
try { sidebarState.expandedFolders = new Set(JSON.parse(localStorage.getItem('sf_open_folders') || '[]')); } catch (_) {}

function saveExpandedFolders() {
  try { localStorage.setItem('sf_open_folders', JSON.stringify([...sidebarState.expandedFolders])); } catch (_) {}
}

function setSidebarOpen(open) {
  document.documentElement.classList.toggle('sidebar-closed', !open);
  $('sidebarScrim').hidden = !open || !isNarrowScreen();
  if (!isNarrowScreen()) {
    try { localStorage.setItem('sf_sidebar_closed', open ? '0' : '1'); } catch (_) {}
  }
}

function toggleSidebar() {
  setSidebarOpen(document.documentElement.classList.contains('sidebar-closed'));
}

function storedSidebarClosed() {
  try { return localStorage.getItem('sf_sidebar_closed') === '1'; } catch (_) { return false; }
}
setSidebarOpen(!(isNarrowScreen() || storedSidebarClosed()));

let wasNarrowScreen = isNarrowScreen();
window.addEventListener('resize', () => {
  const narrow = isNarrowScreen();
  if (narrow === wasNarrowScreen) return;
  wasNarrowScreen = narrow;
  setSidebarOpen(narrow ? false : !storedSidebarClosed());
  if (narrow) toggleControls(false);
});

$('sidebarCloseBtn').onclick = () => setSidebarOpen(false);
$('sidebarOpenBtn').onclick = () => setSidebarOpen(true);
$('sidebarScrim').onclick = () => setSidebarOpen(false);
$('newChatBtn').onclick = () => { clearChat(); if (isNarrowScreen()) setSidebarOpen(false); els.input.focus(); };

document.querySelectorAll('[data-open-tab]').forEach(btn => {
  btn.onclick = () => openSettings(btn.dataset.openTab);
});

let sidebarSearchTimer = null;
$('sidebarSearch').addEventListener('input', () => {
  clearTimeout(sidebarSearchTimer);
  sidebarSearchTimer = setTimeout(() => {
    sidebarState.query = $('sidebarSearch').value.trim();
    refreshSidebar();
  }, SEARCH_DEBOUNCE_MS);
});

async function refreshSidebar() {
  try {
    const q = sidebarState.query ? '?q=' + encodeURIComponent(sidebarState.query) : '';
    const [{ sessions = [] }, { folders = [] }] = await Promise.all([
      apiJson('/api/chat/sessions' + q),
      apiJson('/api/folders'),
    ]);
    sidebarState.sessions = sessions;
    sidebarState.folders = folders;
    renderSidebar();
  } catch (e) {
    $('chatGroups').innerHTML = `<p class="settings-hint error">failed to load chats: ${escapeHtml(e.message)}</p>`;
  }
}

function dateGroupLabel(iso) {
  const updated = new Date(iso);
  if (isNaN(updated)) return 'Older';
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const age = startOfToday - updated;
  if (age <= 0) return 'Today';
  if (age <= DAY_MS) return 'Yesterday';
  if (age <= 7 * DAY_MS) return 'Previous 7 days';
  if (age <= 30 * DAY_MS) return 'Previous 30 days';
  return updated.toLocaleString(undefined, { month: 'long', year: 'numeric' });
}

function chatItem(session) {
  const li = document.createElement('li');
  li.className = 'chat-item' + (session.id === currentSessionId ? ' active' : '');
  li.draggable = true;
  li.dataset.sessionId = session.id;
  const branch = session.parent_session_id ? '<span class="chat-item-branch" title="Branched from another chat">&#8618; </span>' : '';
  const tags = session.tags && session.tags.length ? `<span class="chat-item-tags">#${session.tags.map(escapeHtml).join(' #')}</span>` : '';
  li.innerHTML =
    `<button type="button" class="chat-item-link" title="${escapeHtml(session.title || '(untitled)')}">${branch}${escapeHtml(session.title || '(untitled)')}${tags}</button>` +
    `<button type="button" class="icon-btn small chat-item-menu-btn" aria-label="Chat options" title="Chat options">&middot;&middot;&middot;</button>`;
  li.querySelector('.chat-item-link').onclick = () => { openSession(session.id); if (isNarrowScreen()) setSidebarOpen(false); };
  li.querySelector('.chat-item-link').ondblclick = () => renameChat(session);
  li.querySelector('.chat-item-menu-btn').onclick = e => {
    e.stopPropagation();
    openChatMenu(session, e.currentTarget);
  };
  li.addEventListener('dragstart', e => {
    e.dataTransfer.setData('text/x-starfire-chat', session.id);
    e.dataTransfer.effectAllowed = 'move';
  });
  return li;
}

function makeDropTarget(el, folderId) {
  el.addEventListener('dragover', e => {
    if (![...e.dataTransfer.types].includes('text/x-starfire-chat')) return;
    e.preventDefault();
    el.classList.add('drop-target');
  });
  el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
  el.addEventListener('drop', e => {
    el.classList.remove('drop-target');
    const sessionId = e.dataTransfer.getData('text/x-starfire-chat');
    if (!sessionId) return;
    e.preventDefault();
    moveChatToFolder(sessionId, folderId);
  });
}

function renderSidebar() {
  const { sessions, folders, query } = sidebarState;
  const pinned = sessions.filter(s => s.pinned);
  const folderIds = new Set(folders.map(f => f.id));
  const inFolder = s => s.folder_id && folderIds.has(s.folder_id);

  $('pinnedSection').hidden = !pinned.length;
  const pinnedList = $('pinnedList');
  pinnedList.innerHTML = '';
  pinned.forEach(s => pinnedList.appendChild(chatItem(s)));

  const folderList = $('folderList');
  folderList.innerHTML = '';
  if (!folders.length) folderList.innerHTML = '<li class="folder-empty">no folders - drag chats into one once made</li>';
  for (const folder of folders) {
    const chats = sessions.filter(s => s.folder_id === folder.id && !s.pinned);
    const open = sidebarState.expandedFolders.has(folder.id) || (query && chats.length);
    const li = document.createElement('li');
    const row = document.createElement('div');
    row.className = 'folder-row';
    row.innerHTML =
      `<button type="button" class="folder-toggle" aria-expanded="${open ? 'true' : 'false'}">${open ? '&#9662;' : '&#9656;'} ${escapeHtml(folder.name)} <span class="chat-item-tags">${chats.length}</span></button>` +
      `<button type="button" class="icon-btn small folder-menu-btn" aria-label="Folder options" title="Folder options">&middot;&middot;&middot;</button>`;
    row.querySelector('.folder-toggle').onclick = () => {
      if (sidebarState.expandedFolders.has(folder.id)) sidebarState.expandedFolders.delete(folder.id);
      else sidebarState.expandedFolders.add(folder.id);
      saveExpandedFolders();
      renderSidebar();
    };
    row.querySelector('.folder-menu-btn').onclick = e => {
      e.stopPropagation();
      openFolderMenu(folder, e.currentTarget);
    };
    makeDropTarget(row, folder.id);
    li.appendChild(row);
    if (open) {
      const children = document.createElement('ul');
      children.className = 'folder-children';
      if (!chats.length) children.innerHTML = '<li class="folder-empty">empty</li>';
      chats.forEach(s => children.appendChild(chatItem(s)));
      li.appendChild(children);
    }
    folderList.appendChild(li);
  }

  const groupsEl = $('chatGroups');
  groupsEl.innerHTML = '';
  const loose = sessions.filter(s => !s.pinned && !inFolder(s));
  if (!sessions.length) {
    groupsEl.innerHTML = `<p class="folder-empty">${query ? 'no matching chats' : 'no chats yet'}</p>`;
  }
  let currentGroup = null;
  let list = null;
  for (const s of loose) {
    const label = dateGroupLabel(s.updated);
    if (label !== currentGroup) {
      currentGroup = label;
      const section = document.createElement('section');
      section.className = 'sidebar-section';
      section.innerHTML = `<h4 class="sidebar-heading">// ${escapeHtml(label.toLowerCase())}</h4>`;
      list = document.createElement('ul');
      list.className = 'chat-list';
      section.appendChild(list);
      groupsEl.appendChild(section);
    }
    list.appendChild(chatItem(s));
  }
  updateDocumentTitle();
}

makeDropTarget($('chatGroups'), '');

function updateDocumentTitle() {
  const title = currentSessionId ? currentChatTitle() : '';
  document.title = title ? title + ' · starfire.ai' : 'starfire.ai';
}

function currentChatTitle() {
  const session = sidebarState.sessions.find(s => s.id === currentSessionId);
  if (session) return session.title;
  const first = messages.find(m => m.role === 'user');
  return first ? (first.display ?? first.content).slice(0, 60) : 'New chat';
}

function onChatChanged() {
  renderSidebar();
  const hash = currentSessionId ? '#chat=' + currentSessionId : '';
  if (location.hash !== hash) history.replaceState(null, '', location.pathname + location.search + hash);
  if (isNarrowScreen()) setSidebarOpen(false);
}

async function updateChat(sessionId, fields) {
  try {
    await apiJson('/api/chat/sessions/' + sessionId, { method: 'PUT', body: fields });
  } catch (e) {
    printLine('error: ' + e.message, 'err');
  }
  await refreshSidebar();
}

async function renameChat(session) {
  const title = await showDialog({ title: 'rename chat', input: session.title, okLabel: 'rename' });
  if (title) updateChat(session.id, { title: title.slice(0, 120) });
}

async function editChatTags(session) {
  const raw = await showDialog({
    title: 'tags',
    text: 'Comma-separated, e.g. work, python. Leave empty to clear.',
    input: (session.tags || []).join(', '),
    okLabel: 'save',
    allowEmpty: true,
  });
  if (raw === null) return;
  const tags = raw.split(',').map(t => t.trim()).filter(Boolean);
  updateChat(session.id, { tags });
}

function moveChatToFolder(sessionId, folderId) {
  if (folderId) {
    sidebarState.expandedFolders.add(folderId);
    saveExpandedFolders();
  }
  updateChat(sessionId, { folder_id: folderId });
}

async function cloneChat(session) {
  try {
    const clone = await apiJson(`/api/chat/sessions/${session.id}/clone`, { method: 'POST' });
    await refreshSidebar();
    openSession(clone.id);
  } catch (e) {
    printLine('error: ' + e.message, 'err');
  }
}

async function archiveChat(session, archived = true) {
  await updateChat(session.id, { archived });
  if (archived && session.id === currentSessionId) clearChat();
}

async function deleteChat(session) {
  const ok = await showDialog({
    title: 'delete chat',
    text: `Delete "${session.title}"? This cannot be undone.`,
    okLabel: 'delete', danger: true,
  });
  if (!ok) return;
  try {
    await apiJson('/api/chat/sessions/' + session.id, { method: 'DELETE' });
  } catch (e) {
    printLine('error: ' + e.message, 'err');
  }
  if (session.id === currentSessionId) clearChat();
  refreshSidebar();
}

async function downloadChat(session, format) {
  try {
    const full = await apiJson('/api/chat/sessions/' + session.id);
    const base = 'starfire-' + (full.title || 'chat').replace(/[^\w-]+/g, '_').slice(0, 40);
    if (format === 'json') {
      downloadText(base + '.json', JSON.stringify([full], null, 2), 'application/json');
    } else {
      const text = full.messages.map(m => '### ' + m.role + '\n\n' + (m.display ?? m.content)).join('\n\n---\n\n');
      downloadText(base + '.md', text, 'text/plain');
    }
  } catch (e) {
    printLine('error: ' + e.message, 'err');
  }
}

function showContextMenu(anchor, items) {
  const menu = $('chatMenu');
  menu.innerHTML = '';
  for (const item of items) {
    if (item.heading) {
      const h = document.createElement('div');
      h.className = 'menu-heading';
      h.textContent = item.heading;
      menu.appendChild(h);
      continue;
    }
    if (item.separator) {
      const sep = document.createElement('div');
      sep.className = 'menu-sep';
      menu.appendChild(sep);
      continue;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('role', 'menuitem');
    btn.textContent = item.label;
    if (item.danger) btn.classList.add('danger');
    btn.onclick = () => { menu.hidden = true; item.run(); };
    menu.appendChild(btn);
  }
  closeAllMenus(menu);
  menu.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const top = Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8);
  const left = Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8);
  menu.style.top = Math.max(8, top) + 'px';
  menu.style.left = Math.max(8, left) + 'px';
  const first = menu.querySelector('button');
  if (first) first.focus();
}

$('chatMenu').addEventListener('keydown', e => {
  const buttons = [...$('chatMenu').querySelectorAll('button')];
  const at = buttons.indexOf(document.activeElement);
  if (e.key === 'Escape') { $('chatMenu').hidden = true; return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
  }
});

function openChatMenu(session, anchor) {
  const folderItems = sidebarState.folders
    .filter(f => f.id !== session.folder_id)
    .map(f => ({ label: '→ ' + f.name, run: () => moveChatToFolder(session.id, f.id) }));
  if (session.folder_id) folderItems.push({ label: '→ (no folder)', run: () => moveChatToFolder(session.id, '') });
  showContextMenu(anchor, [
    { label: session.pinned ? 'unpin' : 'pin', run: () => updateChat(session.id, { pinned: !session.pinned }) },
    { label: 'rename', run: () => renameChat(session) },
    { label: 'clone', run: () => cloneChat(session) },
    { label: 'tags…', run: () => editChatTags(session) },
    ...(folderItems.length ? [{ heading: 'move to folder' }, ...folderItems] : []),
    { separator: true },
    { label: 'download .json', run: () => downloadChat(session, 'json') },
    { label: 'download .md', run: () => downloadChat(session, 'md') },
    { label: 'archive', run: () => archiveChat(session) },
    { label: 'delete', danger: true, run: () => deleteChat(session) },
  ]);
}

async function createFolder() {
  const name = await showDialog({ title: 'new folder', input: '', okLabel: 'create' });
  if (!name) return;
  try {
    const folder = await apiJson('/api/folders', { method: 'POST', body: { name } });
    sidebarState.expandedFolders.add(folder.id);
    saveExpandedFolders();
  } catch (e) {
    printLine('error: ' + e.message, 'err');
  }
  refreshSidebar();
}

function openFolderMenu(folder, anchor) {
  showContextMenu(anchor, [
    { label: 'new chat here', run: () => startChatInFolder(folder.id) },
    { label: 'rename', run: async () => {
      const name = await showDialog({ title: 'rename folder', input: folder.name, okLabel: 'rename' });
      if (!name) return;
      try { await apiJson('/api/folders/' + folder.id, { method: 'PUT', body: { name } }); }
      catch (e) { printLine('error: ' + e.message, 'err'); }
      refreshSidebar();
    } },
    { label: 'delete folder', danger: true, run: async () => {
      const ok = await showDialog({
        title: 'delete folder',
        text: `Delete "${folder.name}"? Its chats are kept and move back to the main list.`,
        okLabel: 'delete', danger: true,
      });
      if (!ok) return;
      try { await apiJson('/api/folders/' + folder.id, { method: 'DELETE' }); }
      catch (e) { printLine('error: ' + e.message, 'err'); }
      refreshSidebar();
    } },
  ]);
}

let pendingFolderId = '';

function takePendingFolderId() {
  const folderId = pendingFolderId;
  pendingFolderId = '';
  return folderId;
}

function startChatInFolder(folderId) {
  clearChat();
  pendingFolderId = folderId;
  els.input.focus();
}

$('newFolderBtn').onclick = createFolder;

const archivedEls = { modal: $('archivedModal'), list: $('archivedList'), search: $('archivedSearch') };

async function refreshArchived() {
  const q = archivedEls.search.value.trim();
  try {
    const { sessions = [] } = await apiJson('/api/chat/sessions?archived=true' + (q ? '&q=' + encodeURIComponent(q) : ''));
    archivedEls.list.innerHTML = sessions.length ? '' : '<li class="settings-hint">no archived chats</li>';
    for (const s of sessions) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(s.title || '(untitled)')}</span>` +
        `<span class="endpoint-meta">${new Date(s.updated).toLocaleDateString()}</span>` +
        '<button class="btn ghost" data-act="open" type="button">open</button>' +
        '<button class="btn ghost" data-act="restore" type="button">unarchive</button>' +
        '<button class="endpoint-remove" type="button" title="Delete">×</button>';
      li.querySelector('[data-act="open"]').onclick = () => { archivedEls.modal.hidden = true; openSession(s.id); };
      li.querySelector('[data-act="restore"]').onclick = async () => { await archiveChat(s, false); refreshArchived(); };
      li.querySelector('.endpoint-remove').onclick = async () => { await deleteChat(s); refreshArchived(); };
      archivedEls.list.appendChild(li);
    }
  } catch (e) {
    archivedEls.list.innerHTML = `<li class="settings-hint error">${escapeHtml(e.message)}</li>`;
  }
}

function openArchived() {
  archivedEls.modal.hidden = false;
  if (isNarrowScreen()) setSidebarOpen(false);
  refreshArchived();
}

$('archivedBtn').onclick = openArchived;
$('openArchivedBtn').onclick = openArchived;
$('archivedCloseBtn').onclick = () => { archivedEls.modal.hidden = true; };
archivedEls.modal.addEventListener('click', e => { if (e.target === archivedEls.modal) archivedEls.modal.hidden = true; });
let archivedSearchTimer = null;
archivedEls.search.addEventListener('input', () => {
  clearTimeout(archivedSearchTimer);
  archivedSearchTimer = setTimeout(refreshArchived, SEARCH_DEBOUNCE_MS);
});
$('unarchiveAllBtn').onclick = async () => {
  try { await apiJson('/api/chat/sessions/unarchive-all', { method: 'POST' }); }
  catch (e) { printLine('error: ' + e.message, 'err'); }
  refreshArchived();
  refreshSidebar();
};

async function refreshChatManagement() {
  const cloud = $('tagCloud');
  try {
    const { tags = [] } = await apiJson('/api/chat/tags');
    cloud.innerHTML = tags.length ? '' : '<p class="settings-hint">no tags yet - add them from a chat\'s ··· menu</p>';
    for (const tag of tags) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chip';
      btn.innerHTML = `<span class="chip-label">#${escapeHtml(tag.name)} · ${tag.count}</span>`;
      btn.onclick = () => {
        $('sidebarSearch').value = 'tag:' + tag.name;
        sidebarState.query = 'tag:' + tag.name;
        refreshSidebar();
        closeSettings();
        setSidebarOpen(true);
      };
      cloud.appendChild(btn);
    }
  } catch (e) {
    cloud.innerHTML = `<p class="settings-hint error">${escapeHtml(e.message)}</p>`;
  }
}

function setHint(el, text, kind) {
  el.textContent = text;
  el.className = 'settings-hint' + (kind ? ' ' + kind : '');
}

$('exportAllChatsBtn').onclick = () => {
  const a = Object.assign(document.createElement('a'), { href: '/api/chat/sessions/export' });
  a.click();
};

$('importChatsBtn').onclick = async () => {
  const result = $('importChatsResult');
  const file = $('importChatsFile').files[0];
  if (!file) { setHint(result, 'choose a .json file first', 'error'); return; }
  try {
    const parsed = JSON.parse(await file.text());
    const chats = Array.isArray(parsed) ? parsed : [parsed];
    const { imported, skipped } = await apiJson('/api/chat/sessions/import', { method: 'POST', body: { chats } });
    setHint(result, `imported ${imported} chat(s)` + (skipped ? `, skipped ${skipped} with no messages` : ''), 'ok');
    refreshSidebar();
    refreshChatManagement();
  } catch (e) {
    setHint(result, e instanceof SyntaxError ? 'that file is not valid JSON' : e.message, 'error');
  }
};

$('archiveAllChatsBtn').onclick = async () => {
  const ok = await showDialog({ title: 'archive all chats', text: 'Move every chat to the archive? You can unarchive them later.', okLabel: 'archive all' });
  if (!ok) return;
  try {
    const { archived } = await apiJson('/api/chat/sessions/archive-all', { method: 'POST' });
    setHint($('archiveAllResult'), `archived ${archived} chat(s)`, 'ok');
    clearChat();
    refreshSidebar();
  } catch (e) {
    setHint($('archiveAllResult'), e.message, 'error');
  }
};

$('deleteAllChatsBtn').onclick = async () => {
  const typed = await showDialog({
    title: 'delete all chats',
    text: 'This permanently deletes every chat, archived ones included. Type "delete all" to confirm.',
    input: '', okLabel: 'delete everything', danger: true,
  });
  if (typed !== 'delete all') {
    if (typed !== null) setHint($('deleteAllResult'), 'not deleted - the confirmation text did not match', 'error');
    return;
  }
  try {
    const { deleted } = await apiJson('/api/chat/sessions?confirm=delete-all', { method: 'DELETE' });
    setHint($('deleteAllResult'), `deleted ${deleted} chat(s)`, 'ok');
    clearChat();
    refreshSidebar();
  } catch (e) {
    setHint($('deleteAllResult'), e.message, 'error');
  }
};

let promptCache = [];
let editingPromptId = null;

async function loadPrompts() {
  try {
    ({ prompts: promptCache = [] } = await apiJson('/api/prompts'));
  } catch (_) {
    promptCache = [];
  }
  return promptCache;
}

function resetPromptForm() {
  editingPromptId = null;
  $('promptCommand').value = '';
  $('promptTitle').value = '';
  $('promptContent').value = '';
  $('promptFormTitle').textContent = 'new prompt';
  $('cancelPromptEditBtn').hidden = true;
}

async function refreshPromptList() {
  const list = $('promptList');
  await loadPrompts();
  list.innerHTML = promptCache.length ? '' : '<li class="settings-hint">no prompts yet</li>';
  for (const p of promptCache) {
    const li = document.createElement('li');
    li.className = 'endpoint-row';
    li.innerHTML =
      `<span class="endpoint-label">/${escapeHtml(p.command)}</span>` +
      `<span class="endpoint-meta">${escapeHtml(p.title)}</span>` +
      '<button class="btn ghost" type="button" data-act="edit">edit</button>' +
      '<button class="endpoint-remove" type="button" title="Delete">×</button>';
    li.querySelector('[data-act="edit"]').onclick = () => {
      editingPromptId = p.id;
      $('promptCommand').value = p.command;
      $('promptTitle').value = p.title;
      $('promptContent').value = p.content;
      $('promptFormTitle').textContent = 'edit /' + p.command;
      $('cancelPromptEditBtn').hidden = false;
    };
    li.querySelector('.endpoint-remove').onclick = async () => {
      const ok = await showDialog({ title: 'delete prompt', text: `Delete /${p.command}?`, okLabel: 'delete', danger: true });
      if (!ok) return;
      try { await apiJson('/api/prompts/' + p.id, { method: 'DELETE' }); }
      catch (e) { setHint($('promptSaveResult'), e.message, 'error'); }
      if (editingPromptId === p.id) resetPromptForm();
      refreshPromptList();
    };
    list.appendChild(li);
  }
}

$('savePromptBtn').onclick = async () => {
  const body = { command: $('promptCommand').value, title: $('promptTitle').value, content: $('promptContent').value };
  try {
    if (editingPromptId) await apiJson('/api/prompts/' + editingPromptId, { method: 'PUT', body });
    else await apiJson('/api/prompts', { method: 'POST', body });
    setHint($('promptSaveResult'), 'saved', 'ok');
    resetPromptForm();
    refreshPromptList();
  } catch (e) {
    setHint($('promptSaveResult'), e.message, 'error');
  }
};
$('cancelPromptEditBtn').onclick = resetPromptForm;

async function expandPromptVariables(content) {
  const now = new Date();
  let clipboard = '';
  if (content.includes('{{CLIPBOARD}}')) {
    try { clipboard = await navigator.clipboard.readText(); } catch (_) { clipboard = ''; }
  }
  return content
    .replaceAll('{{CURRENT_DATE}}', now.toLocaleDateString())
    .replaceAll('{{CURRENT_TIME}}', now.toLocaleTimeString())
    .replaceAll('{{CURRENT_WEEKDAY}}', now.toLocaleDateString(undefined, { weekday: 'long' }))
    .replaceAll('{{CLIPBOARD}}', clipboard);
}

let presetCache = [];

function modelKey(m) { return m.endpoint_id + '\u0000' + m.id; }

function selectModel(endpointId, modelId) {
  const opt = [...els.modelList.options].find(o => o.value === modelId && o.dataset.endpointId === endpointId);
  if (!opt || opt.selected) return false;
  els.modelList.value = opt.value;
  opt.selected = true;
  els.modelList.dispatchEvent(new Event('change'));
  return true;
}

function storedDefaultModel() {
  try { return JSON.parse(localStorage.getItem('sf_default_model') || 'null'); } catch (_) { return null; }
}

function selectDefaultModel() {
  const preferred = storedDefaultModel();
  if (!preferred) return;
  const opt = [...els.modelList.options].find(o => o.value === preferred.id && o.dataset.endpointId === preferred.endpoint_id);
  if (opt) opt.selected = true;
}

function renderModelPicker() {
  const model = currentModel();
  $('modelPickerLabel').textContent = model ? model.id : (models.length ? 'select a model' : 'no models');
  const preferred = storedDefaultModel();
  const isDefault = model && preferred && preferred.id === model.id && preferred.endpoint_id === model.endpoint_id;
  const star = $('setDefaultModelBtn');
  star.innerHTML = isDefault ? '&#9733;' : '&#9734;';
  star.classList.toggle('active', !!isDefault);
  star.title = isDefault ? 'This is your default model' : 'Set as default model';
  if (!$('modelMenu').hidden) renderModelMenu();
}

function renderModelMenu() {
  const q = $('modelSearch').value.trim().toLowerCase();
  const list = $('modelMenuList');
  const current = currentModel();
  list.innerHTML = '';
  const matches = models.filter(m => !q || m.id.toLowerCase().includes(q) || (m.provider || '').toLowerCase().includes(q));
  for (const m of matches) {
    const li = document.createElement('li');
    li.className = 'model-option' + (current && modelKey(current) === modelKey(m) ? ' selected' : '');
    li.setAttribute('role', 'option');
    const provider = window.providerLabel ? window.providerLabel(m.provider) : m.provider;
    const size = m.size ? (m.size / 1073741824).toFixed(1) + ' GB · ' : '';
    li.innerHTML = `<span class="model-option-name">${escapeHtml(m.id)}</span><span class="model-option-meta">${escapeHtml(size + provider)}</span>`;
    li.onclick = () => {
      selectModel(m.endpoint_id, m.id);
      closeAllMenus();
    };
    list.appendChild(li);
  }
  const presets = presetCache.filter(p => !q || p.name.toLowerCase().includes(q));
  if (presets.length) {
    const heading = document.createElement('li');
    heading.className = 'model-menu-heading';
    heading.textContent = 'workspace models (presets)';
    list.appendChild(heading);
    for (const p of presets) {
      const li = document.createElement('li');
      li.className = 'model-option';
      li.setAttribute('role', 'option');
      li.innerHTML = `<span class="model-option-name">${escapeHtml(p.name)}</span><span class="model-option-meta">${escapeHtml(p.model || '')}</span>`;
      li.onclick = () => {
        applyPreset(p);
        els.modelList.dispatchEvent(new Event('change'));
        refreshActiveTools();
        closeAllMenus();
      };
      list.appendChild(li);
    }
  }
  if (!list.children.length) list.innerHTML = '<li class="model-menu-heading">no matches</li>';
  const first = list.querySelector('.model-option');
  if (first) first.classList.add('highlight');
}

async function openModelMenu() {
  const menu = $('modelMenu');
  closeAllMenus(menu);
  menu.hidden = false;
  $('modelPickerBtn').setAttribute('aria-expanded', 'true');
  $('modelSearch').value = '';
  renderModelMenu();
  $('modelSearch').focus();
  try {
    ({ presets: presetCache = [] } = await apiJson('/api/presets'));
    if (!menu.hidden) renderModelMenu();
  } catch (_) {}
}

$('modelPickerBtn').onclick = () => {
  if ($('modelMenu').hidden) openModelMenu();
  else closeAllMenus();
};
$('modelSearch').addEventListener('input', renderModelMenu);
$('modelSearch').addEventListener('keydown', e => {
  const options = [...$('modelMenuList').querySelectorAll('.model-option')];
  const at = options.findIndex(o => o.classList.contains('highlight'));
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!options.length) return;
    const next = Math.max(0, Math.min(options.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)));
    options.forEach((o, i) => o.classList.toggle('highlight', i === next));
    options[next].scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (options[at]) options[at].click();
  } else if (e.key === 'Escape') {
    closeAllMenus();
    $('modelPickerBtn').focus();
  }
});

$('setDefaultModelBtn').onclick = () => {
  const model = currentModel();
  if (!model) return;
  try { localStorage.setItem('sf_default_model', JSON.stringify({ endpoint_id: model.endpoint_id, id: model.id })); } catch (_) {}
  renderModelPicker();
};

const CHAT_PARAMS = [
  { key: 'temperature', label: 'temperature', step: '0.05', min: '0', max: '2' },
  { key: 'top_p', label: 'top p', step: '0.05', min: '0', max: '1' },
  { key: 'top_k', label: 'top k', step: '1', min: '0' },
  { key: 'num_predict', label: 'max tokens', step: '1', min: '1' },
  { key: 'num_ctx', label: 'context length', step: '256', min: '256' },
  { key: 'repeat_penalty', label: 'repeat penalty', step: '0.05', min: '0' },
  { key: 'seed', label: 'seed', step: '1', min: '0' },
];

let chatParams = {};
try { chatParams = JSON.parse(localStorage.getItem('sf_params') || '{}') || {}; } catch (_) {}

function saveChatParams() {
  try { localStorage.setItem('sf_params', JSON.stringify(chatParams)); } catch (_) {}
}

function renderParamGrid() {
  const grid = $('paramGrid');
  grid.innerHTML = '';
  for (const param of CHAT_PARAMS) {
    const id = 'param_' + param.key;
    const label = document.createElement('label');
    label.htmlFor = id;
    label.textContent = param.label;
    const input = document.createElement('input');
    Object.assign(input, { id, type: 'number', step: param.step, placeholder: 'default' });
    if (param.min) input.min = param.min;
    if (param.max) input.max = param.max;
    input.value = chatParams[param.key] ?? '';
    input.addEventListener('input', () => {
      const value = input.value === '' ? null : Number(input.value);
      if (value === null || Number.isNaN(value)) delete chatParams[param.key];
      else chatParams[param.key] = value;
      saveChatParams();
    });
    grid.append(label, input);
  }
}

function chatOptions() {
  const options = {};
  for (const { key } of CHAT_PARAMS) {
    if (typeof chatParams[key] === 'number') options[key] = chatParams[key];
  }
  return Object.keys(options).length ? options : undefined;
}

function toggleControls(force) {
  const panel = $('controlsPanel');
  panel.hidden = typeof force === 'boolean' ? !force : !panel.hidden;
  $('controlsBtn').classList.toggle('active', !panel.hidden);
}

$('controlsBtn').onclick = () => toggleControls();
$('controlsCloseBtn').onclick = () => toggleControls(false);
$('resetParamsBtn').onclick = () => { chatParams = {}; saveChatParams(); renderParamGrid(); };
renderParamGrid();

$('exportBtn').onclick = e => {
  e.stopPropagation();
  const menu = $('exportMenu');
  const opening = menu.hidden;
  closeAllMenus();
  menu.hidden = !opening;
};
$('exportMenu').querySelectorAll('[data-format]').forEach(btn => {
  btn.onclick = () => { $('exportMenu').hidden = true; exportChat(btn.dataset.format); };
});

const TOOL_TOGGLES = [
  ['toggleSearchWeb', 'web search'],
  ['toggleDeepResearch', 'deep research'],
  ['toggleSearchDocuments', 'search knowledge'],
  ['toggleManageMemory', 'memory'],
  ['toggleManageNotes', 'notes & to-dos'],
  ['toggleManageTasks', 'scheduled tasks'],
  ['toggleManageEmail', 'email'],
  ['toggleGenerateImage', 'image generation'],
  ['toggleFindImage', 'find images'],
  ['toggleGenerateAudio', 'speech generation'],
  ['toggleGenerateVideo', 'video generation'],
  ['toggleGenerateDocument', 'document generation'],
  ['toggleGithubCli', 'github cli'],
  ['toggleEditFile', 'edit files (writes to disk)'],
  ['toggleRunShell', 'run shell (no sandbox)'],
];

function renderPlusMenuTools() {
  const box = $('plusMenuTools');
  box.innerHTML = '';
  for (const [id, label] of TOOL_TOGGLES) {
    const source = $(id);
    if (!source) continue;
    const row = document.createElement('label');
    row.innerHTML = `<input type="checkbox" ${source.checked ? 'checked' : ''} /> <span>${escapeHtml(label)}</span>`;
    row.querySelector('input').addEventListener('change', () => { source.click(); });
    box.appendChild(row);
  }
}

function refreshActiveTools() {
  const box = $('activeTools');
  box.innerHTML = '';
  for (const [id, label] of TOOL_TOGGLES) {
    const source = $(id);
    if (!source || !source.checked) continue;
    const chip = document.createElement('span');
    chip.className = 'chip tool';
    chip.innerHTML = `<span class="chip-label">${escapeHtml(label)}</span><button type="button" aria-label="Turn off ${escapeHtml(label)}">×</button>`;
    chip.querySelector('button').onclick = () => source.click();
    box.appendChild(chip);
  }
  if (enabledMcpServers.size) {
    const chip = document.createElement('span');
    chip.className = 'chip tool';
    chip.innerHTML = `<span class="chip-label">${enabledMcpServers.size} MCP server(s)</span>`;
    box.appendChild(chip);
  }
}

TOOL_TOGGLES.forEach(([id]) => { const el = $(id); if (el) el.addEventListener('change', refreshActiveTools); });

$('plusBtn').onclick = e => {
  e.stopPropagation();
  const menu = $('plusMenu');
  const opening = menu.hidden;
  closeAllMenus();
  if (opening) renderPlusMenuTools();
  menu.hidden = !opening;
};

$('plusMenu').addEventListener('click', e => {
  const act = e.target.closest('[data-act]');
  if (!act) return;
  $('plusMenu').hidden = true;
  if (act.dataset.act === 'upload') $('attachFileInput').click();
  else if (act.dataset.act === 'knowledge') insertTrigger('#');
  else if (act.dataset.act === 'prompts') insertTrigger('/');
  else if (act.dataset.act === 'tool-settings') openSettings('tools');
});

function insertTrigger(char) {
  const input = els.input;
  if (char === '/') input.value = '/';
  else input.value += (input.value && !/\s$/.test(input.value) ? ' ' : '') + char;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  updateComposerPopup();
}

const TEXT_EXTENSIONS = /\.(txt|md|markdown|csv|tsv|json|ya?ml|toml|ini|cfg|conf|log|xml|html?|css|js|mjs|ts|tsx|jsx|py|rb|go|rs|java|kt|c|h|cpp|hpp|cs|php|sh|bash|zsh|sql|lua|swift|r|tex)$/i;

let attachments = [];
let attachmentCounter = 0;
let mentionModel = null;

function renderAttachments() {
  const box = $('attachChips');
  box.innerHTML = '';
  if (mentionModel) {
    const chip = document.createElement('span');
    chip.className = 'chip mention';
    chip.innerHTML = `<span class="chip-label">@${escapeHtml(mentionModel.id)}</span><button type="button" aria-label="Remove model mention">×</button>`;
    chip.querySelector('button').onclick = () => { mentionModel = null; renderAttachments(); };
    box.appendChild(chip);
  }
  for (const a of attachments) {
    const chip = document.createElement('span');
    chip.className = 'chip' + (a.status === 'loading' ? ' pending' : '') + (a.status === 'error' ? ' error' : '');
    const note = a.status === 'loading' ? ' (reading…)' : a.note ? ` (${a.note})` : '';
    chip.title = a.status === 'error' ? a.note : a.name;
    chip.innerHTML = `<span class="chip-label">${escapeHtml(a.name + note)}</span><button type="button" aria-label="Remove ${escapeHtml(a.name)}">×</button>`;
    chip.querySelector('button').onclick = () => {
      attachments = attachments.filter(x => x !== a);
      renderAttachments();
    };
    box.appendChild(chip);
  }
}

function limitText(text) {
  if (text.length <= MAX_ATTACHMENT_CHARS) return { text, truncated: false };
  return { text: text.slice(0, MAX_ATTACHMENT_CHARS), truncated: true };
}

const MEDIA_TYPE = /^(image|audio|video)\//;

async function readAttachment(file) {
  if (MEDIA_TYPE.test(file.type)) {
    const form = new FormData();
    form.append('file', file);
    const media = await apiJson('/api/attachments', { method: 'POST', body: form });
    return { media: { id: media.id, kind: media.kind, name: media.name }, text: '', note: media.kind };
  }
  if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
    const form = new FormData();
    form.append('file', file);
    const doc = await apiJson('/api/documents', { method: 'POST', body: form });
    const { text, truncated } = await apiJson(`/api/documents/${doc.id}/text`);
    return { text, note: truncated ? 'added to knowledge, truncated' : 'added to knowledge' };
  }
  const raw = await file.text();
  if (!(file.type.startsWith('text/') || TEXT_EXTENSIONS.test(file.name)) && raw.includes('\u0000')) {
    throw new Error('binary file - only text and PDF files can be attached');
  }
  const { text, truncated } = limitText(raw);
  return { text, note: truncated ? 'truncated' : '' };
}

async function addAttachmentFiles(files) {
  for (const file of files) {
    const entry = { id: ++attachmentCounter, name: file.name, status: 'loading', text: '', note: '' };
    attachments.push(entry);
    renderAttachments();
    try {
      Object.assign(entry, await readAttachment(file), { status: 'ready' });
    } catch (e) {
      Object.assign(entry, { status: 'error', note: e.message });
    }
    renderAttachments();
  }
}

async function attachKnowledge(doc) {
  const entry = { id: ++attachmentCounter, name: '#' + doc.filename, status: 'loading', text: '', note: '' };
  attachments.push(entry);
  renderAttachments();
  try {
    const { text, truncated } = await apiJson(`/api/documents/${doc.id}/text`);
    Object.assign(entry, { text, note: truncated ? 'truncated' : '', status: 'ready' });
  } catch (e) {
    Object.assign(entry, { status: 'error', note: e.message });
  }
  renderAttachments();
}

$('attachFileInput').addEventListener('change', e => {
  addAttachmentFiles([...e.target.files]);
  e.target.value = '';
});

const chatArea = document.querySelector('.chat-area');
chatArea.addEventListener('dragover', e => {
  if ([...e.dataTransfer.types].includes('Files')) e.preventDefault();
});
chatArea.addEventListener('drop', e => {
  if (!e.dataTransfer.files.length) return;
  e.preventDefault();
  addAttachmentFiles([...e.dataTransfer.files]);
});
els.input.addEventListener('paste', e => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  e.preventDefault();
  addAttachmentFiles(files);
});

function takeComposerExtras(text) {
  const ready = attachments.filter(a => a.status === 'ready');
  const overrideModel = mentionModel;
  attachments = attachments.filter(a => a.status === 'loading');
  mentionModel = null;
  renderAttachments();
  if (!ready.length) return { overrideModel, extra: {} };
  const textFiles = ready.filter(a => !a.media);
  const media = ready.filter(a => a.media).map(a => a.media);
  const extra = { files: ready.filter(a => !a.media || a.media.kind !== 'image').map(a => a.name) };
  if (textFiles.length) {
    const blocks = textFiles.map(a => `<file name="${a.name.replace(/"/g, "'")}">\n${a.text}\n</file>`).join('\n\n');
    extra.content = `${text}\n\nAttached files:\n\n${blocks}`;
  }
  if (media.length) extra.attachments = media;
  return { overrideModel, extra };
}

const popupEls = { box: $('composerPopup'), title: $('composerPopupTitle'), list: $('composerPopupList') };
let popupItems = [];
let popupIndex = 0;
let popupTrigger = null;
let documentCache = null;

function closeComposerPopup() {
  popupEls.box.hidden = true;
  documentCache = null;
  popupItems = [];
  popupTrigger = null;
}

function currentTrigger() {
  const input = els.input;
  const before = input.value.slice(0, input.selectionStart);
  let m = before.match(/^\/([\w-]*)$/);
  if (m) return { char: '/', query: m[1].toLowerCase(), start: 0 };
  m = before.match(/(^|\s)([#@])([^\s#@]*)$/);
  if (m) return { char: m[2], query: m[3].toLowerCase(), start: before.length - m[3].length - 1 };
  return null;
}

async function updateComposerPopup() {
  const trigger = currentTrigger();
  if (!trigger) { closeComposerPopup(); return; }
  popupTrigger = trigger;
  let items = [];
  if (trigger.char === '/') {
    if (!promptCache.length) await loadPrompts();
    popupEls.title.textContent = 'prompts';
    items = promptCache
      .filter(p => p.command.startsWith(trigger.query) || p.title.toLowerCase().includes(trigger.query))
      .map(p => ({ key: '/' + p.command, sub: p.title, pick: () => pickPrompt(p) }));
    if (!promptCache.length) items = [{ key: 'no prompts yet', sub: 'add some in workspace → prompts', pick: () => openSettings('prompts') }];
  } else if (trigger.char === '#') {
    if (!documentCache) {
      try { ({ documents: documentCache = [] } = await apiJson('/api/documents')); } catch (_) { documentCache = []; }
    }
    popupEls.title.textContent = 'knowledge';
    items = documentCache
      .filter(d => d.filename.toLowerCase().includes(trigger.query))
      .map(d => ({ key: '#' + d.filename, sub: d.chunk_count + ' chunk(s)', pick: () => pickDocument(d) }));
    if (!documentCache.length) items = [{ key: 'no documents yet', sub: 'upload some in workspace → knowledge', pick: () => openSettings('documents') }];
  } else {
    popupEls.title.textContent = 'models';
    items = models
      .filter(m => m.id.toLowerCase().includes(trigger.query))
      .map(m => ({ key: '@' + m.id, sub: window.providerLabel ? window.providerLabel(m.provider) : m.provider, pick: () => pickMention(m) }));
  }
  if (currentTrigger()?.start !== trigger.start) return;
  popupItems = items;
  popupIndex = 0;
  renderComposerPopup();
}

function renderComposerPopup() {
  popupEls.list.innerHTML = '';
  if (!popupItems.length) { closeComposerPopup(); return; }
  popupItems.forEach((item, idx) => {
    const li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.className = idx === popupIndex ? 'active' : '';
    li.innerHTML = `<span class="popup-key">${escapeHtml(item.key)}</span><span class="popup-sub">${escapeHtml(item.sub || '')}</span>`;
    li.onmousedown = e => { e.preventDefault(); item.pick(); };
    popupEls.list.appendChild(li);
  });
  popupEls.box.hidden = false;
}

function removeTriggerText() {
  const input = els.input;
  const end = input.selectionStart;
  const start = popupTrigger ? popupTrigger.start : end;
  input.value = input.value.slice(0, start) + input.value.slice(end);
  input.setSelectionRange(start, start);
}

async function pickPrompt(prompt) {
  closeComposerPopup();
  els.input.value = await expandPromptVariables(prompt.content);
  els.input.dispatchEvent(new Event('input'));
  els.input.focus();
}

function pickDocument(doc) {
  removeTriggerText();
  closeComposerPopup();
  attachKnowledge(doc);
  els.input.focus();
}

function pickMention(model) {
  removeTriggerText();
  closeComposerPopup();
  mentionModel = model;
  renderAttachments();
  els.input.focus();
}

els.input.addEventListener('input', () => { updateComposerPopup(); });
els.input.addEventListener('blur', () => { setTimeout(closeComposerPopup, 150); });

els.form.addEventListener('keydown', e => {
  if (popupEls.box.hidden || !popupItems.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    e.stopPropagation();
    popupIndex = (popupIndex + (e.key === 'ArrowDown' ? 1 : -1) + popupItems.length) % popupItems.length;
    renderComposerPopup();
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    e.stopPropagation();
    popupItems[popupIndex].pick();
  } else if (e.key === 'Escape') {
    e.stopPropagation();
    closeComposerPopup();
  }
}, true);

const SUGGESTIONS = [
  { title: 'Explain a concept', sub: 'like I\'m new to it', text: 'Explain how DNS resolution works, step by step, like I\'m new to networking.' },
  { title: 'Write some code', sub: 'with tests', text: 'Write a Python function that de-duplicates a list while keeping order, with a few pytest tests.' },
  { title: 'Summarise text', sub: 'paste or attach it', text: 'Summarise the following in five bullet points:\n\n' },
  { title: 'Plan something', sub: 'a week, a trip, a project', text: 'Help me plan a realistic week: I want to fit in three workouts and two evenings of side-project work.' },
];

window.renderSuggestions = container => {
  if (!container || !prefs.showSuggestions || !models.length) return;
  container.innerHTML = '';
  for (const s of SUGGESTIONS) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'suggestion';
    btn.innerHTML = `<span class="suggestion-title">${escapeHtml(s.title)}</span><span class="suggestion-sub">${escapeHtml(s.sub)}</span>`;
    btn.onclick = () => {
      els.input.value = s.text;
      els.input.dispatchEvent(new Event('input'));
      els.input.focus();
      els.input.setSelectionRange(s.text.length, s.text.length);
    };
    container.appendChild(btn);
  }
};

const CallRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let callActive = false;
let callRecognition = null;

function callModeActive() { return callActive; }

const CALL_LABELS = { listening: 'listening…', thinking: 'thinking…', speaking: 'Arty is speaking' };

function setCallState(state, heard) {
  $('callOverlay').dataset.state = state;
  $('callState').textContent = CALL_LABELS[state];
  if (heard !== undefined) $('callHeard').textContent = heard;
}

function britishVoice() {
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  return voices.find(v => v.lang === 'en-GB' && /male|daniel|arthur|george/i.test(v.name))
    || voices.find(v => v.lang === 'en-GB') || null;
}

function listenForCallTurn() {
  if (!callActive || busy) return;
  setCallState('listening');
  try { callRecognition.start(); } catch (_) {  }
}

function stopCall() {
  callActive = false;
  $('callBtn').classList.remove('active');
  $('callOverlay').hidden = true;
  try { callRecognition.abort(); } catch (_) {}
  if (window.speechSynthesis) window.speechSynthesis.cancel();
}

function speakThenListen(text) {
  if (!window.speechSynthesis) { listenForCallTurn(); return; }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = britishVoice();
  if (voice) utterance.voice = voice;
  utterance.onstart = () => setCallState('speaking');
  utterance.onend = () => setTimeout(listenForCallTurn, 250);
  window.speechSynthesis.speak(utterance);
}

if (CallRecognition && window.speechSynthesis) {
  callRecognition = new CallRecognition();
  callRecognition.continuous = false;
  callRecognition.interimResults = false;
  callRecognition.lang = navigator.language || 'en-US';
  callRecognition.onresult = e => {
    const transcript = e.results[0][0].transcript.trim();
    if (!transcript || !callActive) return;
    setCallState('thinking', `“${transcript}”`);
    send(transcript);
  };
  callRecognition.onerror = e => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      stopCall();
      printLine('voice call needs microphone permission', 'err');
    }
  };
  callRecognition.onend = () => { if (callActive && !busy && !window.speechSynthesis.speaking) setTimeout(listenForCallTurn, 300); };
  $('callBtn').hidden = false;
  $('callBtn').onclick = () => {
    if (callActive) { stopCall(); return; }
    if (!currentModel()) return;
    callActive = true;
    $('callBtn').classList.add('active');
    $('callOverlay').hidden = false;
    setCallState('listening', '');
    listenForCallTurn();
  };
  $('callEnd').onclick = stopCall;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && callActive) stopCall(); });
}

els.output.addEventListener('scroll', () => {
  const distance = els.output.scrollHeight - els.output.scrollTop - els.output.clientHeight;
  $('scrollBottomBtn').hidden = distance < SCROLL_BUTTON_THRESHOLD_PX;
});
$('scrollBottomBtn').onclick = () => { els.output.scrollTo({ top: els.output.scrollHeight, behavior: 'smooth' }); };

function copyText(text) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => setStatus('ready', 'copied'), () => setStatus('error', 'copy failed'));
}

document.addEventListener('keydown', e => {
  const key = e.key.toLowerCase();
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.shiftKey && key === 'o') {
    e.preventDefault();
    clearChat();
    els.input.focus();
  } else if (mod && e.shiftKey && key === 's') {
    e.preventDefault();
    toggleSidebar();
  } else if (mod && e.shiftKey && key === 'c') {
    e.preventDefault();
    const last = [...messages].reverse().find(m => m.role === 'assistant');
    if (last) copyText(last.content);
  } else if (mod && e.shiftKey && (key === ';' || key === ':')) {
    e.preventDefault();
    const blocks = els.output.querySelectorAll('.line.ai pre code');
    if (blocks.length) copyText(blocks[blocks.length - 1].textContent);
  } else if (e.shiftKey && key === 'escape') {
    e.preventDefault();
    els.input.focus();
  } else if (key === 'escape') {
    closeAllMenus();
    if (!archivedEls.modal.hidden) archivedEls.modal.hidden = true;
  }
});

const chatFromHash = (location.hash.match(/^#chat=([\w-]+)$/) || [])[1];
if (chatFromHash) currentSessionId = chatFromHash;

refreshActiveTools();
refreshSidebar();
loadPrompts();
