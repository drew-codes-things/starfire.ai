'use strict';

const SETTINGS_TAB_LISTS = {
  providers: ['endpointList'],
  tools: ['mcpServerList'],
  memory: ['memoryList'],
  documents: ['documentList'],
  notes: ['noteTemplateList', 'noteList'],
  automations: ['taskList', 'taskRunList'],
  email: ['emailAccountList', 'ruleList'],
  presets: ['presetList'],
  prompts: ['promptList'],
  localgen: ['workflowList'],
  accounts: ['accountList'],
};

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
  (SETTINGS_TAB_LISTS[tab.dataset.tab] || []).forEach(id => showListLoading(document.getElementById(id)));
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
        `<span class="endpoint-meta">${s.transport && s.transport !== 'stdio' ? escapeHtml(s.url) + ' · ' : ''}${s.tool_count} tool(s)${s.connected ? '' : ' · not running'}${s.secrets_missing ? ' · secret missing, remove and add it again' : ''}</span>` +
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
