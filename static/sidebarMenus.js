'use strict';

let contextMenuAnchor = null;

function closeContextMenu() {
  $('chatMenu').hidden = true;
  if (contextMenuAnchor && contextMenuAnchor.isConnected) contextMenuAnchor.focus();
}

function showContextMenu(anchor, items) {
  const menu = $('chatMenu');
  menu.innerHTML = '';
  for (const item of items) {
    if (item.heading) {
      const h = document.createElement('div');
      h.className = 'menu-heading';
      h.textContent = sentenceCase(item.heading);
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
    btn.textContent = sentenceCase(item.label);
    if (item.danger) btn.classList.add('danger');
    btn.onclick = () => { menu.hidden = true; item.run(); };
    menu.appendChild(btn);
  }
  closeAllMenus(menu);
  menu.hidden = false;
  contextMenuAnchor = anchor;
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
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
  }
});
$('chatMenu').addEventListener('keydown', closeOnEscape(closeContextMenu));

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
    showToast(e.message, 'error');
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
      catch (e) { showToast(e.message, 'error'); }
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
      catch (e) { showToast(e.message, 'error'); }
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
        '<button class="endpoint-remove" type="button" title="Delete" aria-label="Delete">×</button>';
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
  catch (e) { showToast(e.message, 'error'); }
  refreshArchived();
  refreshSidebar();
};
