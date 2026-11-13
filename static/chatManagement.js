'use strict';

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
