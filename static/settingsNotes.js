'use strict';

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
