'use strict';

const tempChatToggle = $('tempChatToggle');

function isTemporaryChat() { return tempChatToggle.checked || isSideChatOpen(); }

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
  else $('controlsScrim').hidden = true;
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

function chatItem(session) {
  const li = document.createElement('li');
  li.className = 'chat-item' + (session.id === currentSessionId ? ' active' : '');
  li.draggable = true;
  li.dataset.sessionId = session.id;
  const branch = session.parent_session_id ? '<span class="chat-item-branch" title="Branched from another chat">&#8618; </span>' : '';
  const tags = session.tags && session.tags.length ? `<span class="chat-item-tags">#${session.tags.map(escapeHtml).join(' #')}</span>` : '';
  li.innerHTML =
    `<button type="button" class="chat-item-link" title="${escapeHtml(session.title || '(untitled)')}">${branch}<span class="chat-item-title">${escapeHtml(session.title || '(untitled)')}</span>${tags}</button>` +
    `<button type="button" class="icon-btn small chat-item-menu-btn" aria-label="Chat options" aria-haspopup="menu" title="Chat options">${icon('more', 'solid')}</button>`;
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
  if (!folders.length) folderList.innerHTML = '<li class="folder-empty">No folders yet</li>';
  for (const folder of folders) {
    const chats = sessions.filter(s => s.folder_id === folder.id && !s.pinned);
    const open = sidebarState.expandedFolders.has(folder.id) || (query && chats.length);
    const li = document.createElement('li');
    const row = document.createElement('div');
    row.className = 'folder-row';
    row.innerHTML =
      `<button type="button" class="folder-toggle" aria-expanded="${open ? 'true' : 'false'}">${icon(open ? 'chevron-down' : 'chevron-right')}<span class="chat-item-title">${escapeHtml(folder.name)}</span><span class="chat-item-tags">${chats.length}</span></button>` +
      `<button type="button" class="icon-btn small folder-menu-btn" aria-label="Folder options" aria-haspopup="menu" title="Folder options">${icon('more', 'solid')}</button>`;
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
      if (!chats.length) children.innerHTML = '<li class="folder-empty">Empty</li>';
      chats.forEach(s => children.appendChild(chatItem(s)));
      li.appendChild(children);
    }
    folderList.appendChild(li);
  }

  const groupsEl = $('chatGroups');
  groupsEl.innerHTML = '';
  const loose = sessions.filter(s => !s.pinned && !inFolder(s));
  if (!sessions.length) {
    groupsEl.innerHTML = `<p class="folder-empty">${query ? 'No chats match that search' : 'No chats yet. Send a message to start one.'}</p>`;
  }
  let currentGroup = null;
  let list = null;
  for (const s of loose) {
    const label = dateGroupLabel(s.updated);
    if (label !== currentGroup) {
      currentGroup = label;
      const section = document.createElement('section');
      section.className = 'sidebar-section';
      section.innerHTML = `<h4 class="sidebar-heading">${escapeHtml(label)}</h4>`;
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
  updateControlsScope();
  const hash = currentSessionId ? '#chat=' + currentSessionId : '';
  if (location.hash !== hash) history.replaceState(null, '', location.pathname + location.search + hash);
  if (isNarrowScreen()) setSidebarOpen(false);
}

async function updateChat(sessionId, fields) {
  try {
    await apiJson('/api/chat/sessions/' + sessionId, { method: 'PUT', body: fields });
  } catch (e) {
    showToast(e.message, 'error');
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
    showToast(e.message, 'error');
  }
}

async function archiveChat(session, archived = true) {
  await updateChat(session.id, { archived });
  if (archived && isChatOpen(session.id)) clearChat();
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
    showToast(e.message, 'error');
  }
  if (isChatOpen(session.id)) clearChat();
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
    showToast(e.message, 'error');
  }
}
