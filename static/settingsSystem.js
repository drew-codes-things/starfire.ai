'use strict';

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
