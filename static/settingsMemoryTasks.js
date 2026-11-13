'use strict';

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
  settingsEls.memoryAddResult.textContent = '';
  settingsEls.memoryAddResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/memory', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, category: settingsEls.newMemoryCategory.value }),
    });
    if (!r.ok) throw new Error(await failureMessage(r, `could not add the memory (server error ${r.status})`));
  } catch (e) {
    settingsEls.memoryAddResult.textContent = e.message;
    settingsEls.memoryAddResult.className = 'settings-hint error';
    return;
  }
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
